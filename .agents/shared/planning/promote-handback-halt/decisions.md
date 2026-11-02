# Decision Register — `promote-handback-halt`

**Commission:** `promote-handback-halt` — promote-pipeline fix lane (serving-green hand-back halt / wrongful-rollback / wrongful-quarantine)
**Date:** 2026-11-02
**Author:** worker via plan-creation (decisions consolidation)
**Status:** Draft — for architect review + council pressure-test (next gate)
**Plan set:** `.agents/shared/planning/promote-handback-halt/` — `phase1-plan.md`, `phase2-plan.md`, `phase3-plan.md`
**Base tree:** `941cb6321` (== `origin/latest` tip at lane creation) · worktree `/home/nea/ensemble-src-wt-promote-handback-halt` · branch `fix/promote-handback-halt`
**Artifact status:** PLANNING / ANALYSIS ONLY. No code modified. No `launcher.sh` / `promote.sh` / `lib.sh` executed. No live-install (`/home/nea/agents-ensemble`) contact. All verification was read-only (`sed`/`grep`/`git status` inside the worktree).

---

## 1. HEADER

### 1.1 Commission summary

On 2026-11-01 a promote of **v0.28.5** flipped the release and then **halted at the hand-back verifier**. The verifier's deadline was borrowed from an unrelated knob (`LIVEZ_BUDGET_S` = 180 s, `scripts/upgrade/lib.sh:4753` ← `lib.sh:146`) while the flipped unit took **~3 min** from unit-active to serving (MCP warmup + auto-continue resume). The daemon process started at `21:31:00.27` and the port bound *after* the ~`21:31:10` deadline — a miss by seconds (KV `census_corrections.real_abort_predicate`). The v0.28.3 ceremony (`r-20261101-075210-e3dd`) had confirmed at **175/180 s** — the same race, nearly lost (phase1-plan.md:33; KV `census_code_sites.test_pins_to_update`).

The halt left the txn **open** with `flipped=true`. The target was **serving all-green** (receipt `21:49:50Z` — livez `alive v0.28.5` + readyz all-components-true, zero reasons; KV `census_code_sites.serving_green_receipt`). At `22:25:53Z` an operator restart booted the launcher; the launcher-boot sweep saw a stale flipped txn (age **3540 s**, computed — *not* a constant) and took the **AUTO-ROLLBACK** disposition: repoint `current` → `v0.28.4`, **quarantine the healthy `v0.28.5`** (`launcher.sh:1218`), count a rollback + arm a cooldown (`launcher.sh:1224`), write a `sweep_rollback` row (`launcher.sh:1225-1226`), clear `in_flight` (`launcher.sh:1229-1230`). The next re-promote was refused: *"version 'v0.28.5' is QUARANTINED — cleared only by re-staging"* (`scripts/upgrade/lib.sh:6559-6562`).

The commission is a **three-phase plan set** that fixes the three distinct decision-makers involved:

| Phase | Owns | Decision surface |
|---|---|---|
| **Phase 1** | the **hand-back verifier** | `scripts/upgrade/lib.sh:4751-4782` (`_supervision_unit_serving`), `scripts/upgrade/promote.sh:262`/`:459`/`:587` |
| **Phase 2** | the **disposition chain** (launcher sweep / daemon janitor sweep / promote-entry adopt) | `launcher.sh:830-1258`, `daemon/services/upgrade_journal_sweep.py`, `daemon/api.py`, `daemon/tools/upgrade_journal.py`, `scripts/upgrade/lib.sh:6559-6626` |
| **Phase 3** | the **cooldown rider** + the consolidated test-surface map + the deployment mapping | `launcher.sh:459-478`, `scripts/upgrade/stage.sh`, `daemon/tools/upgrade_tools.py:3916` |

### 1.2 Run-id correction (recorded)

`phase3-plan.md` carries a **typo'd incident run id** in two places:

- `phase3-plan.md:3` — *"**Commission:** `promote-handback-halt` (incident `r-20261021-212542-8279`, target v0.28.5)"*
- `phase3-plan.md:22` — *"## 2. Background (incident `r-20261021-212542-8279`, 2026-11-01)"*

The **correct** id is **`r-20261101-212542-8279`** (2026-11-01, not 2026-10-21). Authority: KV `incident_2026_11_01.run_id` = `"r-20261101-212542-8279"`, corroborated by `phase1-plan.md:29` and `phase2-plan.md:19`, both of which carry the correct id. The typo is a **date transposition** (`10-21` ↔ `11-01`), not a different run.

> **Disposition:** this register and every downstream artifact use `r-20261101-212542-8279`. The typo in `phase3-plan.md` is **recorded here, not repaired in place** — the commission explicitly forbids modifying the three phase files. The correction is a **doc-only** rider candidate for the implementing lane (see `OQ-C-20`, which already carries a doc-only correction of the same class).

### 1.3 Binding owner rulings (VERBATIM — do not re-litigate)

From KV `design_rulings_owner_ratified`:

> **"USER RULING (Discord 2026-11-01, RATIFIED — binding at plan gate, not a leaning): 'Don't roll back if that isn't needed — when the new version is working and living, there is no reason to roll back.' => DISPOSITION PRINCIPLE: SERVING-GREEN = STAY (commit-close); rollback reserved EXCLUSIVELY for genuinely unhealthy serving (failed gates at promote time, unhealthy livez/readyz post-flip)."**

> **"Req (c) settled directionally by the ruling: the halt-path quarantine must NOT punish a release that flipped and served green — quarantine_cleared_serving_green (new explicit journaled path, no bypass of the frozen re-stage-only rule) aligns; carry BOTH the ruling and this shape into the architect review as owner-ratified semantics."**

> **"COMPOSITION REQUIREMENT (Ari): the new serving-green protection COMPOSES with the house never-green protection — never-green MUST still roll back a broken release (that path is untouched and stays armed); serving-green MUST NOT be rolled back. THE PIN SET MUST PROVE BOTH DIRECTIONS: (i) stale flipped txn + serving-green → commit-close, no rollback, no quarantine punishment; (ii) stale flipped txn + serving-unhealthy → existing rollback path still fires."**

**Compliance of the three phases against these three rulings is verified in §4.5–§4.6 below.** No phase contradicts them; where a phase makes a choice that *appears* adjacent to the ruling (notably Phase 2's defer-ceiling rollback), §3 `OQ-C-06` records why it is not a contradiction.

### 1.4 Inputs

| # | Input | Used for |
|---|-------|----------|
| 1 | `.agents/shared/planning/promote-handback-halt/phase1-plan.md` (674 lines) | D-P1-1..10, tasks T-P1-1..11, risks R-P1-1..12, legs L1-L12, mutations M1-M10, AC-P1-1..16, OQ-P1-1..5 |
| 2 | `.agents/shared/planning/promote-handback-halt/phase2-plan.md` (421 lines) | D.0-D.7, adjudications A1-A6, T1-T13, P-1..P-12, R-1..R-12, S-1..S-10, matrix rows 1-10, OQ-1..OQ-10 |
| 3 | `.agents/shared/planning/promote-handback-halt/phase3-plan.md` (487 lines) | P3-D1..D9, P3-R1..R7, P3-T01..T12, AC-P3-1..12, P3-PIN-01..17, OQ P3-Q1..P3-Q10, §4.4 cross-phase reconciliation |
| 4 | `shared_meta_kv()` — keys `design_rulings_owner_ratified`, `design_guidance_leader`, `incident_2026_11_01`, `census_code_sites`, `census_corrections`, `sharpened_reqs_part2`, `research_C_daemon` | the binding rulings; the incident record; the code-site census + its corrections; the daemon-side census; the leader's two-part guidance |

**Independently verified read-only in the worktree** (spot-checks against the plans' citations): `launcher.sh:385-395` (field-creation refusal), `:459-478` (cooldown defect), `:515-536` (`_js_history_has_intent_flip`), `:1030-1040` (the "a good outcome is not a bad outcome" doctrine), `:1065-1082` (commit-and-continue gate), `:1125-1135` / `:1205-1235` (the AUTO-ROLLBACK chain), `:60-95` (sweep constants), `:1505-1565` (run_loop child restart), `:1655-1670` (`_journal_sweep` called once); `scripts/upgrade/lib.sh:90-100` (`PIPELINE_SCRIPTS_REV`), `:2095-2115` (canonical cooldown twin), `:2135-2155` (`journal_cooldown_active`), `:4745-4785` (`_supervision_unit_serving`), `:6550-6566` (`promote_entry_check`); `daemon/api.py:2845-2860` (sweep wiring), `:5432-5452` (`/livez`); `daemon/tools/upgrade_journal.py:375-392` (`journal_update_field` KeyError), `:410-422` (`ensure_extensions`), `daemon/tools/upgrade_tools.py:3910-3920` (`PIPELINE_SCRIPTS_FLOOR_COMMIT`); `daemon/services/upgrade_journal_sweep.py:90-120` (sweep constants), `:1948-1975` (`sweep_once`); `scripts/systemd/ensemble-daemon.service` (unit policy); `.agents/tester/rules/ensure.md:10-30` (tier doctrine).

---

## 2. DECISIONS MADE (consolidated register)

### 2.1 How to read this register

- **IDs are preserved, never renumbered.** Phase prefixes are kept exactly as authored: `D-P1-n` (Phase 1), `D.n` (Phase 2, unprefixed), `P3-Dn` (Phase 3). Per **P3-Q10** (recorded as `OQ-C-25`): the consolidated set keeps the `P3-` prefix and carries the cross-reference table (phase3-plan.md §4.4.1) rather than flattening; if a flat sequence is later produced, new ids are **appended**, existing ones preserved.
- **Phase 2's `D.0`-`D.7` are section-decisions** (the design sections carry the decision text), Phase 2's `A1`-`A6` are **binding adjudications on the implementer** (phase2-plan.md §D.2), and `T5` is a **recorded omission** (a decision *not* to implement).
- Every row carries its **key anchor(s)** so the architect can pressure-test against code without re-reading the phase files.

### 2.2 Phase 1 — hand-back verify budget + serving conjunct

| ID | Phase | Decision (one line) | Key anchor(s) |
|---|-------|---------------------|---------------|
| **D-P1-1** | P1 | Dedicated knob `HANDBACK_VERIFY_BUDGET_S` (default **300**), declared via `_numeric_knob`, replacing the verifier's borrow of `LIVEZ_BUDGET_S`. | `lib.sh:180` (declaration site), `lib.sh:4753` (the borrow being replaced), `lib.sh:146` (`LIVEZ_BUDGET_S`=180), `lib.sh:109-123` (`_numeric_knob` house pattern); measured boot ~185 s (phase1-plan.md:92-96) |
| **D-P1-2** | P1 | Effective budget is **deadline-aware and monotone-safe**: `clamp(ceremony_remaining − TAIL_RESERVE, FLOOR=180, CEILING=300)`; no ceremony clock ⇒ `CEILING`. | `lib.sh:632-640` (`_effective_ceremony_remaining_s`), `lib.sh:6040`+`promote.sh:680` (adaptive-soak precedent) |
| **D-P1-3** | P1 | The ceremony clock reaches the verifier via a **plain (non-exported)** shell variable `PROMOTE_CEREMONY_DEADLINE_EPOCH`, set by `promote.sh` beside `:262`. | `promote.sh:262`, `lib.sh:1376-1379` (`JOURNAL_READ_STRICT` precedent) |
| **D-P1-4** | P1 | The verdict becomes a **real serving check**: port-listener (necessary) **AND** `/livez` 2xx **AND** `version == expected`; `MainPID == prestop` still fails fast in one poll. | `lib.sh:4751-4782`, `lib.sh:4760-4763` (fast-fail, unchanged), `lib.sh:3874-3920`+`:4059`+`:4066`+`:4174` (curl already used in `lib.sh`), `lib.sh:5991-6002` (`gate_version` comparator shape), `daemon/api.py:5215`/`:5448` (livez `version` = `__version__`) |
| **D-P1-5** | P1 | Failure semantics: **transient** ⇒ poll to deadline; **wrong version** ⇒ fail fast; **degenerate** (`PORT` unset, or neither `lsof` nor `curl`) ⇒ MainPID-alone (unchanged). | `lib.sh:3874-3920` (house green/transient/red), `lib.sh:4148` (`_probe`), `lib.sh:4744-4746` ("never fault on a missing tool") |
| **D-P1-6** | P1 | **One** progress-gated extension of ≤ 60 s (`HANDBACK_VERIFY_GRACE_S`), firing only when a new MainPID **and** a bound port are present at first expiry; never a loop. | `lib.sh:4754-4779` (the verifier already *is* a 2 s poll loop — the budget is the retry) |
| **D-P1-7** | P1 | `PROMOTE_TOTAL_DEADLINE_S` **stays 900** under Phase 1; `RuntimeMaxSec` therefore stays **1320** and the L3 invariant is untouched. | `lib.sh:163`, `lib.sh:158` (the house's own "all-budgets-max = 1400s → halt" stop-loss note), `daemon/tools/upgrade_journal.py:2556-2568` (derivation), `:2544-2545` (floor/tail), `daemon/manager.py:5798-5808` (arm site) |
| **D-P1-8** | P1 | The C-10a decomposition comment is **corrected to a real bound table** (the documented `handback 90` is fiction; the code uses 180), and records that the 900 s cap is a stop-loss. **[PLANNER r4 → note (h) THE FICTION EXISTS IN TWO COPIES]** the same C-10a arithmetic block is duplicated in **`promote.sh:255-261`** (verified in-tree: `promote.sh:256-261` carries the identical `preflight 45 / stop 225 / flip 30 / handback 90 / livez 180 / readyz 120 / version 30 / soak 120 / commit 30 = 870s` line) ⇒ **T-P1-9 must correct BOTH copies** (`lib.sh:157-162` **and** `promote.sh:255-261`), or the second copy keeps asserting the fiction. | `lib.sh:157-162` (the defective comment — copy 1), **`promote.sh:255-261` (the same block — copy 2)**, `lib.sh:4753` (the real slice) |
| **D-P1-9** | P1 | The expected version is plumbed as `PROMOTE_EXPECTED_VERSION` (plain variable, from `BIN_VERSION`); when unset the serving check degrades to "2xx, no version comparison". | `promote.sh:459`, `promote.sh:670` (the same value `gate_version` compares), `lib.sh:4891` (the success claim this makes verifiable) |
| **D-P1-10** | P1 | The new budget names are registered in `_budget_in_force` so a future breach row names the value in force. | `lib.sh:467-476` (resolver; refuses unknown names), `lib.sh:461-466` (the house rationale) |

### 2.3 Phase 2 — serving-green split closer

| ID | Phase | Decision (one line) | Key anchor(s) |
|---|-------|---------------------|---------------|
| **D.0** | P2 | **Three** decision-makers after this phase, with different observability: launcher boot sweep (partial — pre-daemon), daemon janitor sweep (**yes** — in-process), promote-entry adopt (**yes** — `lib.sh` is not purity-constrained). | `launcher.sh:1661-1662` (sweep runs before the daemon child at `:1525`), `upgrade_journal_sweep.py:96-99` (90 s tick), `lib.sh:3841-3860` (classified probe) |
| **D.1** | P2 | **Daemon-side closer**: a *separate lane* in `sweep_once` with its own narrow predicate (**P1-P9** incl. `SWEEP_STALE_S=600` mirror, owner-dead, boot-grace, **and P9 = the `intent_flip` authorization conjunct** — **[PLANNER r5 → BLOCKER 1]** the predicate list now ends at **P9**, not P8; the `intent_flip` conjunct existed only inside AM-P2-4, so an implementer building from this row would ship a closer that **commit-closes on health alone**; **Absent P9 ⇒ no-action-this-tick — never commit, never verdict, never request**), an **in-process** health read (version + cached readiness composite via a new `readiness_provider` kwarg), GREEN ⇒ commit-close (ordered mutations), NOT-GREEN ⇒ `serving_unhealthy` verdict + rollback **disposition** only. | `upgrade_journal_sweep.py:1950-2019` (`sweep_once` seam), `upgrade_journal.py:2035-2036` + `:2262-2272` (the guards it deliberately does **not** widen), `daemon/api.py:5436-5450`/`:5452-5533`/`:2717`/`:4452` (health surfaces), `daemon/services/readiness.py:754-810` (`GraceTracker`), `daemon/constants.py:165` (`READINESS_GRACE_HORIZON_S=120.0` — **[PLANNER r4 → note (b) ANNOTATED: this is the pool-busy load horizon the closer does NOT borrow**; the closer's own horizon is `UPGRADE_CLOSER_BOOT_GRACE_S` (default 300), declared in exactly ONE place — `daemon/services/upgrade_journal_sweep.py` beside the other sweep-local constants (`:96-115`; verified: `:99`/`:105`/`:112`) — and `daemon/constants.py` is **not** its home]), `upgrade_journal_sweep.py:416-424` (per-tick kill-switch precedent), `phase2-plan.md` §D.1 predicate table (the P9 row) + `T8` + `P-6` (the implementer-facing sites, folded in the same pass) |
| **D.2** | P2 | **Launcher-side closer**: a purity-safe `_js_serving_probe` + a serving-green branch placed **first** in the `flipped=true` branch, with a tri-state verdict and the commit-close mutation order **AM-P2-1's history-before-close order**. **[PLANNER r4]** the earlier wording — *"the commit-close mutation order mirrored from the proven path"* — is **SUPERSEDED** (append-only; the text is retained above for the audit trail): the "proven path" (`launcher.sh:1108-1127`) is the **OLD close-before-history** order (verified in-tree: `:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append) ⇒ mirroring it defeats AM-P2-10.3's journal check and reds P-19 / P3-PIN-23. **The correct order is `current` → `previous` → history append `serving_green_commit` (with `quarantine_cleared_serving_green` FIRST when the target is quarantined) → `in_flight`→null → `quarantined[]` rewrite.** | `launcher.sh:975+` (flipped branch), `launcher.sh:568-606` (`_js_run_bounded` — the sweep-hang defense the probe must not reintroduce), `launcher.sh:1108-1127` (**the superseded close-before-history order — cited for the audit trail, NOT as the model**; the C-4 `previous`-write ordering pin at `:1112-1117` still stands), `launcher.sh:1094-1106` (absent/`null` `displaced_version` ⇒ halt-for-human), `launcher.sh:518-536` (`_js_history_has_intent_flip` — the conjunction technique) |
| **D.3** | P2 | **`quarantine_cleared_serving_green`** — the frozen re-stage-only rule is **extended, not bypassed**: amended on **BOTH sides** (daemon rewrites `quarantined[]` + appends the event; shell's `promote_entry_check` accepts iff the event names the version). | `lib.sh:6559-6562` (the refusing gate), `lib.sh:1963-1969` (`journal_is_quarantined`), `lib.sh:1945-1960` (`journal_quarantine_clear`, re-staging-only), `stage.sh:674` (the only existing clear caller), `upgrade_journal.py:375-389` + `:392-407` (writers) |
| **D.4** | P2 | **`adopt_stale_txn` is same-phase treatment, as a separately-scoped task (T11)** — the promote-entry mirror of the same decision table; GREEN ⇒ commit-close + the promote continues, NOT-GREEN ⇒ the existing chain byte-identical. **[PLANNER r5 → SAME-PASS NOTE — the two binding divergences are named at this row (pointer added).]** The row's one-line form cannot carry them, and an implementer reading only this row would assume the adopt path behaves **identically** to the launcher and the daemon. ⇒ **read `phase2-plan.md` §D.4's (a-i) and (a-ii) before building T11:** **(a-i)** the **GREEN-without-`intent_flip`** arm **diverges by design** — the launcher **defers**, the daemon takes **no action this tick**, the adopt path takes the **existing chain** (and R-8's shared-pin claim is **scoped** accordingly); **(a-ii)** the **NOT-GREEN** arm has **no defer and no grace** — that is correct here, because the adopt path engages only a txn already **past** the 600 s staleness gate and is a **promote-entry** decision where the operator is present. Additionally **[PLANNER r5 → sweep class (a)]** the adopt path's **GREEN arm carries the `intent_flip` conjunct** (AM-P2-4's T11 requirement — a NEW `journal_history_has_intent_flip <ver>` reader in `lib.sh`; `lib.sh` has **zero** `intent_flip` readers today), so **GREEN ⇒ commit-close** here means **GREEN ∧ `intent_flip` ⇒ commit-close**. | `lib.sh:6579-6626` (the documented mirror discipline), `lib.sh:6627+` (the stale+flipped branch), `lib.sh:6604` (ADR-024 citation — unchanged), `phase2-plan.md` §D.4 (a-i)/(a-ii) (the two binding divergences) |
| **D.5** | P2 | **Rollback execution for daemon-side unhealthy verdicts** = **Option 2** (journal disposition → next launcher boot executes the existing chain) as primary; **Option 3** (operator runbook) as documented escalation; **Option 1** (transient-unit executor) explicitly **out of scope**. | `launcher.sh:1209-1242` (the chain that already exists), `launcher.sh:1661` vs `:1525` (the launcher executes pre-daemon — the clean point), `daemon/tools/upgrade_journal.py:3044-3113` (`spawn_executor` — the option rejected), `daemon/manager.py:5809` (its only current caller) |
| **D.6** | P2 | **Defer ceiling**: the counter is a **history count** keyed by `run_id` (no new journal field — the launcher cannot create one); default **3**; form `: "${SWEEP_DEFER_CEILING:=3}"` (env-overridable, unlike the mirrored plain assignments); over-count is the safe direction; the CEILING-th encounter executes the existing auto-rollback. **[PLANNER r4 → BLOCKER 4 — SUPERSEDED IN THREE RESPECTS (append-only; the text above is retained for the audit trail). The row as written teaches the superseded mechanism and a refuted doctrine, and contradicts §8.2 / §9.4 of this same register.]** **(1) The counter's HOME is re-homed** — not a journal-history count but the **launcher-local file** `<INSTALL_DIR>/.sweep-defer-<run_id>` (**AM-P2-5**; sibling of `.launcher-state`; O(1); survives launcher restarts; no journal schema change; not in `releases/`). The history-count shape was rejected for three verified defects: cross-entry false positives, the O(N²) cost (measured 13.5 s / 35.7 s at 27 KB ⇒ ≈285× at the incident size) and the absence of any launcher-side size guard. **(2) The "over-count is the safe direction" doctrine is INVERTED** — the counter gates the **rollback itself**, so an over-count fires a **wrongful rollback**; the live `rollback_window_count` is **2/3**, so one false rollback **arms the 24 h promote halt** ⇒ under-count (not over-count) is the safe direction for the *count*, while the *observation* conjuncts (owner-dead ∧ `intent_flip` ∧ never-green-observed) are what keep a wrongful rollback out. **(3) The ceiling's ELIGIBILITY is the bounded three-path set** (**AM-P2-10.5**): **BROKEN ⇒ immediate** · **`rollback_requested` ⇒ immediate** · **AMBIGUOUS ⇒ bounded** — everything else **defers**; there is **no fourth path**. The constant's **name, `:=` form and default (3) stand**; only its *meaning* changed — it is now *"the number of qualifying AMBIGUOUS observations after which the absent-class rollback becomes eligible"*. | `launcher.sh:387-393` (`_js_journal_update` returns 1 on an absent field — the reason a **new field** is impossible), `launcher.sh:69-94` (mirrored constants are plain assignments), `launcher.sh:1658` (`:=`-form precedent), `launcher.sh:78` (`SWEEP_ROLLBACK_CAP_24H=3` — the "three strikes" flavour), `launcher.sh:1144-1147` (the existing over-count doctrine — **the doctrine that AM-P2-5 inverts for this counter**; it still governs the *rollback* counter, which is not what D.6 is about) |
| **D.7** | P2 | **Event vocabulary + alert map**: `sweep_deferred` ∉ terminal-events, no alert; `serving_green_commit` ∈ terminal-events, no alert; `quarantine_cleared_serving_green` ∉ terminal, **new alert kind** recommended; `serving_unhealthy` ∉ terminal, reuse `upgrade_auto_rollback` recommended. **[PLANNER r4 → BLOCKER 4 — "recommended" ⇒ REQUIRED (append-only; the text above is retained for the audit trail).]** The two **alert-map entries are REQUIRED, not recommendations** (**AM-P2-8**): `ALERT_KIND_BY_EVENT` is an **exact dict lookup** (`upgrade_journal.py:758`) ⇒ there is **no** false-`upgrade_auto_rollback` collision; the real hazard is **SILENCE** — without an explicit entry the clearance reverses nothing the operator saw. The final mapping is **AM-P2-19's split**: `serving_unhealthy` → the NEW kind **`upgrade_serving_unhealthy_requested`** (a **request**, not an execution) · `rollback_requested` → the same new kind · `sweep_rollback` (the **executed** rollback) → **`upgrade_auto_rollback`** (unchanged) · `quarantine` → `upgrade_auto_rollback` (unchanged) · `quarantine_cleared_serving_green` → a **NEW kind** · `serving_green_commit` → none (silent) · `sweep_deferred` → none (a hold, not an outcome). | `daemon/tools/upgrade_journal.py:646-651` (alert map), `:758` (the exact-lookup site), `:1874-1884` (`_TERMINAL_EVENTS`, incl. `boot_sweep_commit_and_continue` at `:1883`), `:590-596` ("commit, restart, sweep, nonce_consumed … emit NOTHING") |
| **A1** | P2 | **`curl` is NOT admissible in `launcher.sh`** — R-08 purity is a hard in-file constraint; zero `curl` occurrences today. | `launcher.sh:1040-1053` (the R-08 clause), `launcher.sh:8-15` (self-contained contract) |
| **A2** | P2 | **`bash /dev/tcp` IS admissible** — a bash builtin feature (no external binary), with in-repo precedent. | `tests/helpers/soak_gate_pack_common.sh:215`, `test/packs/hard_reboot_drill_e2e_test.sh:354` |
| **A3** | P2 | **Bound the read** — bash-builtin `read -t <budget>` only, total budget ≤ 5 s; never an unbounded socket read. | `launcher.sh:568-606` (the sweep-hang defense family) |
| **A4** | P2 | **bash 3.2-safe shapes only** — use the `<&3` redirection form, not `read -u fd`; verify/waive the `read -t` behaviour under bash 3.2. | `launcher.sh:266` (Bash 3.2-safe escaper), `launcher.sh:247-263`/`:759-770` (Darwin/BSD dispatch arms) |
| **A5** | P2 | **Version matching strips the leading `v`** — the release target is `v0.28.5`; `__version__` is `0.28.5`. A naive compare is *always false* ⇒ every healthy target looks unhealthy ⇒ wrongful rollback returns. | `daemon/__init__.py:3`, `daemon/api.py:5448`, `daemon/models/common.py:138` |
| **A6** | P2 | The verdict is **tri-state**, and the third state is the load-bearing one: **"not positively green" ⇒ DEFER, never rollback** — the launcher's port-down is ambiguous by construction. | `launcher.sh:1661-1662` vs `:1525` (pre-daemon ⇒ ambiguous), phase2-plan.md §"The Defer-vs-Deadlock Matrix" (a) |
| **T5 (omission)** | P2 | **`_js_quarantine_clear` is NOT added to the launcher** — the launcher never clears a quarantine under this design (the daemon clears it on the GREEN path). Recorded as a decision, not an oversight. | `launcher.sh:481-494` (`_js_quarantine`), `lib.sh:1945-1960` (the twin the launcher would have mirrored) |

### 2.4 Phase 3 — cooldown rider + test map + deployment mapping

| ID | Phase | Decision (one line) | Key anchor(s) |
|---|-------|---------------------|---------------|
| **P3-D1** | P3 | Fix the launcher cooldown with an **epoch-argument two-arm `uname` dispatch** (Option A), in the launcher's own idiom — not `lib.sh`'s relative form. | `launcher.sh:474` (the defect), `:475` (the fallback that writes NOW), `:476` (the write), `:247-263` (`_js_iso_to_epoch` idiom), `:755-770` (`_js_flip_current` idiom), `:1333` (`_now`) |
| **P3-D2** | P3 | The rider's pins assert **property** (born-future + delta band + arm selection), not string equality; a static "no bare `date -jur` outside the BSD arm" pin is the string-level backstop. | `tests/test_boot_sweep_commit_and_continue.sh:329-366` (C7 — its live-verification block exercises only the GNU arm) |
| **P3-D3** | P3 | The rider's pins land in a **NEW dedicated pack** (`tests/test_launcher_cooldown_arm.sh`), with fixtures from `tests/upgrade_test_lib.sh`. | `tests/upgrade_test_lib.sh:33` (`_register_fixture`), `:69-106` (F1 fake-HOME fence), `grep -rn '_js_count_rollback' tests/ scripts/ daemon/` ⇒ **zero** hits today (the function has no pin of any kind) |
| **P3-D4** | P3 | The **cross-component consumer round-trip** is the anchor rider pin — the value the launcher writes must be consumed as an **ACTIVE** cooldown by `lib.sh`. | `scripts/upgrade/lib.sh:2139-2151` (`journal_cooldown_active`), `:6556-6558` (the promote-entry gate that consumes it) |
| **P3-D5** | P3 | The two-sided floor/stamp requirement is enforced by **two executable pins**, not by documentation. | `scripts/upgrade/lib.sh:590-593` (the stamp gate was *extracted from* `stage.sh` specifically so it is executable in a pack), `:594-615` |
| **P3-D6** | P3 | **Tier 2 is mandatory for the lane** (two independent triggers); the rider's own pins stay Tier 1. | `.agents/tester/rules/ensure.md:19-20` (core-seam list incl. `daemon/api.py`; promote-blocking arm), `:16-17` (Tier 1) |
| **P3-D7** | P3 | The operator `.env` pin advance is stated as a **ceremony precondition in the operator-facing doc**, explicitly **user-owned** — not worked around. | `daemon/tools/upgrade_tools.py:3701-3731` (arm-time guard, judged **before** the nonce is burned; `:3707-3712`), `:3723-3733` (fallback ⇒ WARNING only) |
| **P3-D8** | P3 | The rider is implemented **after Phase 2 lands** (or folded in-commit with it), never on the pre-Phase-2 disposition chain. | phase2-plan.md §"Sequencing note (shared surface)"; the two edits touch different functions (`_journal_sweep`'s disposition branch vs `_js_count_rollback`) ⇒ the fold is mechanical |
| **P3-D9** | P3 | The launcher-side serving probe is **`bash /dev/tcp` + bash JSON parsing**; `curl` is admissible only in `lib.sh`. | Phase 2 pin `P-11` (purity scan forbids executable `curl`/`wget`/`nc`/`lsof`/`python3` in `launcher.sh`); `lib.sh` carries no such constraint |

### 2.5 Cross-phase consistency scan — findings

**Tension #1 (named in the commission, now SETTLED).** Phase 1 declares its gate *"Tier 1 (blast-radius leg), ≤ 30 min hard, fail-loud"* and states *"The gate is not promote-blocking, so the Tier-2 trigger does not apply"* (phase1-plan.md §9). Phase 3 §4.4.3 rules the **lane** Tier 2 (core-seam trigger: `daemon/api.py`; release-gate trigger: *"this lane is the promote pipeline"*).

> **Resolution — SETTLED, recorded.** The Tier-2 mandate is a **lane-level** property, not a per-phase one. Phase 1's Tier-1 statement remains **true within Phase 1's own scope in isolation** (Phase 1 touches only `scripts/upgrade/{lib.sh,promote.sh}` + a shell test — no core seam). Phase 2 touches `daemon/api.py` **and** `daemon/manager.py` (both listed core seams) plus `daemon/services/upgrade_journal_sweep.py`, `daemon/services/readiness.py`, `daemon/constants.py`, `daemon/tools/upgrade_journal.py`. The lane therefore escalates mechanically on both arms (`ensure.md:19-20`). **Binding:** P3-PIN-17 (the Tier-2 whole-tree dual-side leg) is mandatory **for the lane**; the per-phase Tier-1 statements are not retracted. (phase3-plan.md §4.4.3.)

**Tension #2 (found by this scan — RECORDED, no contradiction).** Phase 2 §D.6's defer ceiling fires the **existing auto-rollback** after 3 deferrals, which reads against the leader's PART-2 guidance *"NOT positively green … ⇒ DEFER with a journaled deferral row, **DO NOT rollback**"* (KV `design_guidance_leader`). **Not a contradiction:** the owner ruling scopes rollback to *"genuinely unhealthy serving"*; a target that has never served in any of the observed boots is genuinely unhealthy. The defer ceiling is what keeps *"defer"* from becoming *"never roll back"* — phase2-plan.md §matrix row 4. The residual is R-4 (see `OQ-C-06`).

**[PLANNER r5 → BLOCKER 3 — THE PRE-r2 FIRING RULE IS SUPERSEDED, and the "never served ⇒ unhealthy" rationale is REFUTED (append-only; the text above is retained for the audit trail).]** Two distinct problems with the paragraph above, both folded:
1. **The firing rule is the pre-r2 one.** *"fires the existing auto-rollback after 3 deferrals"* is the **history-count** rule that **AM-P2-5 re-homed** (counter = the launcher-local `.sweep-defer-<run_id>` file) and that **AM-P2-10 replaced** with the **bounded absent-ceiling**. The current rule is the **closed three-path set** — **BROKEN ⇒ immediate** · **`rollback_requested` ⇒ immediate** · **AMBIGUOUS ⇒ bounded** — **and everything else defers**; the AMBIGUOUS path's predicate is **≥`SWEEP_DEFER_CEILING` qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed**, **not** "after N deferrals". The canonical ladder is restated **exactly once**, in `phase2-plan.md` **§D.2**; **§9.3** below is the register's pointer to it. (The matrix row 4 this paragraph cites has already been updated with the r3 timing correction: the bounded rollback lands in **≈10–15 min**, not the earlier "≈30–40 min" figure.)
2. **The rationale is refuted in the WRONGFUL-ROLLBACK direction.** *"a target that has never served in any of the observed boots is genuinely unhealthy"* treats **never-observed-served** as **proof of unhealthiness**. It is not — it is **ambiguity by construction** (the launcher's port-down is ambiguous: a broken target vs *this very boot* starting the daemon; the daemon's own boot is legitimately degraded by MCP warmup + auto-continue). The owner ruling protects the **observed** state: **serving-green ⇒ never rolled back, absolute**; **serving-unhealthy ⇒ the existing chain still fires, BOUNDED** (leader-ratified, OQ-C-06(d-r2) / AM-P2-10). ⇒ the correct reading of the tension is the opposite of the one recorded above: the defer ceiling is **not** a licence to roll back a target that has merely **never been observed serving**; it is the **bounded** path by which **genuinely unhealthy** serving is eventually acted upon, and it is additionally gated on `intent_flip` and owner-death. **Nothing in the owner ruling or the COMPOSITION REQUIREMENT changes** — the never-green protection remains armed in **both** directions (the second direction: stale flipped txn + serving-unhealthy ⇒ the existing chain still fires).

**Tension #3 (found by this scan — REGISTER HYGIENE, benign).** Three Phase-3 open questions duplicate decisions already taken in Phase 3's own decision table: **P3-Q1 ≡ P3-D1** (cooldown form), **P3-Q4 ≡ P3-D3** (pin placement), **P3-Q6 ≡ A1 + P3-D9** (probe transport; phase3 itself says *"This is no longer a preference"*). They are **not re-opened** here — see `OQ-C-16`, `OQ-C-19`, `OQ-C-21`.

**Tension #4 (found by this scan — ANCHOR DRIFT, benign but worth one line).** The KV's `research_C_daemon` cites the canonical cooldown twin at `lib.sh:1375-1385`; that range actually holds `JOURNAL_SIZE_GUARD_BYTES` / `JOURNAL_LEGACY_READ_BUDGET_S`. The real dispatch is at **`lib.sh:2099-2107`** (verified). Phase 3 records this correction in its §4.1.5 "Citation correction recorded" and in its §12 appendix. Phase 2's `EXECUTOR_STALENESS_WINDOW_S` anchor is `:110` in the plan vs **`:112`** in the tree (verified) — a one-line drift, no substance.

**No phase contradicts a binding ruling.** Verified: §4.5 (never-green stays armed), §4.6 (no-live-contact), §4.1 (quarantine re-stage-only extended, not bypassed).

**[PLANNER r2 — 2026-11-02] This scan is re-run after the plan council's REJECT (scoped to Phase 2).** The scan's conclusion **held** — no phase contradicts a binding ruling — but it needed one correction, now folded: **§4.5's compliance claim was under-specified** (the never-green path was not *mechanically* armed in the canonical ambiguous shape). The correction, the three ruling adjudications and the reconciled absent-only disposition are recorded in **§9 [PLANNER r2]**.

---

## 3. DECISIONS DELIBERATELY LEFT OPEN

**This is the heart of the register.** Every question the three phases raised is consolidated here — **25 original questions, none dropped**. Phase prefixes are preserved in the cross-ref column; numbering `OQ-C-n` is this register's own.

**Gate vocabulary:**
- **ARCHITECT** — design pressure-test (the shape, the seams, the trade-offs). The architect pressure-tests this register next.
- **LEADER** — project-policy call (budgets, windows, thresholds, packaging, debt laundering).
- **USER** — owner/decision authority (anything that changes the owner-ratified ruling or the ceremony contract).

### 3.0 Priority shortlist (the ones that gate the plan)

| # | Question | Gate | Why it is first |
|---|---|---|---|
| `OQ-C-06` | Phase-2 `OQ-1` — defer ceiling + the R-4 residual | **LEADER** + ARCHITECT | The residual is the *opposite* failure of tonight's (a broken release left live forever). **New information** (see the assessment): R-4 is **bounded in the crash shape** and **unbounded in the tempfail/clean-exit shapes** — neither plan states this. |
| `OQ-C-07` | Phase-2 `OQ-2` — rollback execution for daemon-side unhealthy verdicts | **ARCHITECT** | The commission explicitly demands options + recommendation; it will be pressure-tested. |
| `OQ-C-11` | Phase-2 `OQ-6` — `intent_flip` evidence on the serving-green path | **ARCHITECT** + LEADER | **Security-relevant**: it is the trust anchor between "the daemon is healthy" and "the flip was authorised". |
| `OQ-C-01` | Phase-1 `OQ-P1-1` — raise `PROMOTE_TOTAL_DEADLINE_S` vs squeeze | **LEADER** (+ ARCHITECT) | A **policy** call with a cross-language default coupling (shell + daemon) and a `RuntimeMaxSec` lockstep hazard. |
| `OQ-C-17` | Phase-3 `P3-Q2` — unrecognized-platform arm policy | **ARCHITECT** | **Material defect in the recommendation as written** (see assessment): option (ii) does not deliver what it promises. |
| `OQ-C-24` | Phase-3 `P3-Q9` — re-pin the `daemon/constants.py` seal? | **LEADER** | Must not launder pre-existing base debt into the lane's diff. |
| `OQ-C-22` | Phase-3 `P3-Q7` — same-release requirement (rider + closer) | **LEADER** | Release-packaging; interacts with the ceremony preconditions in §6. |

### 3.1 Phase 1 questions

---

#### `OQ-C-01` — Should `PROMOTE_TOTAL_DEADLINE_S` be raised to cover the honest all-worst sum?

**Cross-ref:** Phase-1 `OQ-P1-1` (phase1-plan.md §12).

**(a) Question.** The ceremony cap is 900 s; the honest all-worst sum after Phase 1 is **1340 s** (phase1-plan.md §4.4.2). Do we (a) raise the cap, (b) leave it and rely on the deadline-aware squeeze, or (c) split it into pre-flip and post-flip clocks?

**(b) Options.**
- **(a)** Raise the default 900 → ~1350, **in lockstep** in `lib.sh:163` **and** `daemon/tools/upgrade_journal.py:2564`; re-pin `tests/unit/tools/test_upgrade_preflight_daemon_side.py:197` (1320) and `:329` (1620); document that the operator override must land in the **install `.env`**.
- **(b)** Leave it at 900; rely on D-P1-2's deadline-aware squeeze (Phase 1's recommendation).
- **(c)** Split the cap into pre-flip and post-flip clocks.

**(c) Owning phase's recommendation.** **(b) for this phase** — the 900 s cap is a documented **stop-loss** (`lib.sh:158`: *"all-budgets-max … = 1400s → halt"*), the incident case is fixed with ~640 s of downstream headroom, and (a) is a leader-level policy change with a cross-language coupling. The residual is the **combined-slow** case (pre-stop 525 s + slow boot), pre-existing (today's all-worst sum is already 1160 s).

**(d) Independent assessment — AGREE with (b) for this phase; DISAGREE that the decision can be closed here.**

I agree with (b) *as a Phase-1 scoping decision*, and I agree with the phase's reasoning about the stop-loss. Two independent additions to the record:

1. **The coupling is sharper than the phase states, and it argues for a LEADER decision now rather than later.** Phase 1 §4.5 enumerates the option-(a) delta work: change both defaults, re-pin two test expectations, and — critically — note that the two readers agree *only when the value reaches the daemon's process environment* (`launcher.sh:136-186` exports every `.env` line at `:186`; the daemon inherits it). An operator who exports `PROMOTE_TOTAL_DEADLINE_S` in an ad-hoc shell gets a **shell-side** value the daemon never sees → the L3 backstop can sit **below** the shell's own deadline → a *new* failure class (systemd SIGTERM → exit 143 + a halt row **without** the `deadline-breach-` prefix; `lib.sh:493` distinguishes the two classes). That is precisely the kind of new failure class this commission exists to end. **Therefore (a) is only safe if the value is set in the install `.env` and the daemon-side default is bumped in the same commit** — i.e. (a) is a *packaging* commitment, not a one-line default edit.
2. **The phase's own "worst case" arithmetic understates the risk it creates.** Under (b), the effective hand-back budget is `clamp(remaining − 120, 180, 300)`. In the combined-slow case the ceremony is already past 900 s when the hand-back starts, so `remaining` clamps at zero → the effective budget is the **floor 180** → the squeeze buys **nothing** in precisely the case it was designed for, and the ceremony breaches at `promote.sh:598`/`:606` with the daemon **up** — *"the same harmful shape as tonight"* (phase1-plan.md §4.4.3). So (b) does not close the combined-slow hole; it only closes the incident case. The phase says this (R-P1-2), and Phase 2's serving-green protection is the structural backstop. That is coherent — but it means **the honest framing is: (b) fixes tonight's case; only (a) fixes the arithmetic.**

**Recommendation to the gate:** take **(b) for the lane** (no cap change, no `RuntimeMaxSec` churn, no test-expectation churn), and record (a) as a **deferred policy decision** for the leader, explicitly framed as *"the ceremony's all-worst path can still exceed its stop-loss by ~440 s"* — not as a closed item. Option (c) is **rejected**: it creates two clocks to reason about, and no phase needs it.

**(e) Gate.** **LEADER** (policy: the "15 min" ceiling documented at `promote.sh:26-27`) with **ARCHITECT** concurrence on the `RuntimeMaxSec` lockstep.
*If (a):* the two defaults move together in one commit, the two test expectations are re-pinned, the `.env`-not-shell operator rule is documented, and the L3 invariant is re-verified (`RuntimeMaxSec` strictly above the ceremony deadline). *If (b):* nothing moves; the combined-slow residual stays disclosed and rides on Phase 2's serving-green protection.

---

#### `OQ-C-02` — Should the serving check ENFORCE the version, or merely RECORD it?

**Cross-ref:** Phase-1 `OQ-P1-2`.

**(a) Question.** A 2xx `/livez` with the **wrong** version: fail the hand-back immediately (enforce), or log it and let the downstream `gate_version` be the only enforcer (record)?

**(b) Options.** (a) enforce unconditionally; (b) record only; (c) enforce when an expected version is available, degrade otherwise.

**(c) Owning phase's recommendation.** **(c)** — makes the hand-back's own success claim (*"serves the flipped release"*, `lib.sh:4891`) verifiable at the point where it is made, without forcing an expected value into the `restart.sh:237` / `rollback.sh:235` contexts where no target release is being promoted.

**(d) Independent assessment — AGREE with (c), and I note the option-(a) reading is not even available.** Phase 1's D-P1-5 already makes a wrong version a **fail-fast** — *"no amount of waiting fixes a wrong-version daemon"* — and D-P1-9's plumbing (`PROMOTE_EXPECTED_VERSION` from `BIN_VERSION` at `promote.sh:459`) is what makes (c) implementable. Option (b) would leave the hand-back asserting *"serves the flipped release"* without checking it — which is the **exact shape of tonight's bug** (a raw port-listener conjunct that proved *"something is bound"*, not *"the right thing is serving"*). Option (a) is worse than (c) only in the no-version contexts, where there is no expected value to enforce against. The `v`-stripping asymmetry (Phase 2's A5) applies here too: `promote.sh:459`'s `binary_version` is the manifest's bare value (`0.28.5`), the same shape `daemon/api.py:5448` reports — **so Phase 1's version comparison does NOT need the `v`-strip that Phase 2's launcher probe does.** Worth stating explicitly, because a well-meaning implementer might apply the strip twice.

**(e) Gate.** **ARCHITECT** (confirm; low risk). *If changed to (b):* the hand-back's success line must stop claiming *"serves the flipped release"* and the wrong-version guard is lost at the hand-back boundary.

---

#### `OQ-C-03` — Should the `PORT`-unset / `lsof`-absent degenerate branch stay MainPID-alone?

**Cross-ref:** Phase-1 `OQ-P1-3`.

**(a) Question.** Tighten the degenerate branch to a failure, or preserve it?

**(b) Options.** (a) preserve MainPID-alone (phase's recommendation); (b) tighten to a failure when `PORT` is set but no probe tool is available.

**(c) Owning phase's recommendation.** **(a)** — the house rule is *"never fault on a missing tool / missing observation"* (`lib.sh:4744-4746`), and `PORT` is genuinely absent on some non-promote paths (the scope→unit self-heal path at `lib.sh:5077`).

**(d) Independent assessment — AGREE with (a), with one tightening the phase already names and this register records as the operative rule.** Phase 1 §4.2 says: *"when `PORT` **is** set and `curl` **is** available, the serving check is mandatory — the `lsof`-absent case degrades to *serving-only*, never to *MainPID-alone*."* That is the correct reading of "never fault on a missing tool": **do not fault on the missing `lsof`, but do not stop using the tools you do have.** The tightening applies to the `PORT`-set + `curl`-present case only; the `PORT`-unset case must stay MainPID-alone. Phase 1's L6d/L6e legs pin exactly this pair. **No change needed** — this question is settled by D-P1-5; it is listed here only because the phase raised it.

**(e) Gate.** **ARCHITECT** (confirm). *If changed to (b):* a host without `lsof` **and** without `curl` would hard-fail every hand-back — a new false-negative class, the mirror of tonight's.

---

#### `OQ-C-04` — Is the progress-gated grace extension (60 s) worth the extra knob?

**Cross-ref:** Phase-1 `OQ-P1-4`.

**(a) Question.** Implement `HANDBACK_VERIFY_GRACE_S` (one progress-gated extension), or rely on the 300 s ceiling + Phase 2's serving-green protection?

**(b) Options.** (a) implement (phase's recommendation); (b) omit.

**(c) Owning phase's recommendation.** **(a)** — one knob, one branch; it fires only while the daemon is visibly coming up; buys ~20 % more margin for a load-sensitive boot. *"If the architect declines it, drop T-P1-7 and legs L9/L10 + mutations M6/M7; nothing else in this phase depends on it."*

**(d) Independent assessment — AGREE with (a).** The margin arithmetic supports it: measured boot ~185 s, ceiling 300 s ⇒ 1.6×; +60 s ⇒ 360 s ⇒ 1.95×. The grace is **progress-gated** (a new MainPID **and** a bound port), so it fires *only* on the "up but not yet answering" shape — which is the shape tonight's miss lived in, and which the raw-`lsof` conjunct was supposed to catch but could not. Two properties make it safe: it is **never** granted to a dead daemon (no MainPID ⇒ no extension), and it is **never** granted more than once. The phase's own scope note is the reason it is cheap to drop if pressed (L9/L10 + M6/M7 are the only dependents) — so this is genuinely low-stakes either way. I agree with (a) and note it does **not** need the architect's blessing to be *safe*, only to be *wanted*.

**(e) Gate.** **ARCHITECT** (want vs not). *If (b):* the ceiling stays 300 s and the residual is a host slower than 1.6× the measured boot; Phase 2's serving-green protection becomes the load-bearing backstop.

---

#### `OQ-C-05` — Should the `HANDBACK_VERIFY_*` knobs live in the install `.env` or be exported per run?

**Cross-ref:** Phase-1 `OQ-P1-5`.

**(a) Question.** Placement of the new knobs.

**(b) Options.** (a) install `.env` (single place for ceremony-wide overrides — what makes the daemon and shell agree); (b) per-run export (shell-local; a sandbox drill can override without touching the install).

**(c) Owning phase's recommendation.** Document the `.env` as the single place for **ceremony-wide** overrides, and keep the **hand-back knobs shell-local** so a sandbox drill can override them per invocation. The question *"matters only if the leader picks option (a) in OQ-P1-1"* — because only then does the daemon-side `RuntimeMaxSec` derivation need the same value.

**(d) Independent assessment — AGREE.** The separation is correct for a reason the phase states but does not generalise: **the hand-back knobs are consumed by exactly one reader (the shell) and are phase-scoped; the ceremony deadline is consumed by two readers (shell + daemon) and is ceremony-scoped.** Putting a phase-scoped knob in the install `.env` would mean a *permanent* install-side setting for a knob that exists to bound one phase's wait — and every install-side knob becomes a cross-generation compatibility surface. If the leader takes `OQ-C-01` option (a), the **only** knob that must move to the `.env` is `PROMOTE_TOTAL_DEADLINE_S` (two readers); the three `HANDBACK_VERIFY_*` knobs and `HANDBACK_TAIL_RESERVE_S` stay shell-local. This register records that as the operative split.

**(e) Gate.** **LEADER** (it is coupled to `OQ-C-01`). *If the leader takes option (a) in OQ-C-01:* `PROMOTE_TOTAL_DEADLINE_S` is documented as `.env`-only; the hand-back knobs remain per-invocation.

---

### 3.2 Phase 2 questions

---

#### `OQ-C-06` — Defer-ceiling default + the R-4 residual (the "launcher stays up, daemon never starts" hole)

**Cross-ref:** Phase-2 `OQ-1`; Phase-2 risk `R-4`; Phase-2 §D.5 option 2b; Phase-2 matrix row 4.

**(a) Question.** How many deferrals before the backstop fires, and what closes the *"the launcher stays up and the daemon never starts"* hole?

**(b) Options.** (a) `CEILING=3`, residual accepted; (b) `CEILING=3` + a run-loop re-sweep hook so the counter advances per child crash; (c) `CEILING=5` + the same hook.

**(c) Owning phase's recommendation.** **CEILING = 3**, form `: "${SWEEP_DEFER_CEILING:=3}"` (env-overridable so the pin can drive it); the residual handled by **option 2b** (accept — the daemon-side closer is an accelerator; the launcher sweep remains the primary rollback engine). Defer (b)'s run-loop re-sweep hook to its own commission. **[PLANNER r5 → BLOCKER 4 — TWO DEFECTS IN THIS RECOMMENDATION (append-only; the text above is retained for the audit trail).]** (1) **The commission-deferral is STALE — the hook IS in this lane.** *"Defer (b)'s run-loop re-sweep hook to its own commission"* predates **AM-P2-7 / `T15`**: the run-loop re-sweep hook was **ruled in** (the leader's OQ-C-06 adjudication, §(d-r2) below) and is a **numbered task in this lane** (`phase2-plan.md` **AM-P2-7** → **T15**, with `AM-P2-10.4`'s qualifying-observation rule and `AM-P2-17`'s scope narrowing + idempotence). ⇒ the deferral sentence is **withdrawn**: **there is no deferred hook commission to track** — T15 *is* the hook. (The **T15-scope** narrowing and the **two operator-bounded residual shapes** — tempfail-75 and clean-exit-0 — are unchanged and remain disclosed in the (d-r2) table below.) (2) **The precedence claim is SUPERSEDED.** *"the launcher sweep remains the primary rollback engine"* is true only of the pre-lane world. Under this plan the **daemon closer normally wins the race** — it ticks every 90 s while the daemon is up and reaches its verdict at the **first tick after the 600 s staleness gate (≈11 min)**, whereas the launcher's bounded backstop needs **≈10–15 min** of *qualifying* observations and reaches only the shapes the daemon cannot (never-started daemon; tempfail-75 / clean-exit-0). The normative precedence statement is `decisions.md` **§6.1** / **§OQ-C-06(d-r2)** and `phase2-plan.md`'s Defer-vs-Deadlock matrix (a) *Launcher/daemon precedence* paragraph. ⇒ **the daemon-side closer is the PRIMARY disposer of a stale flipped txn while the daemon is up; the launcher sweep is the BACKSTOP.** The **CEILING = 3** and the **`:=` form** recommendations are **UNCHANGED** by this annotation.

**(d) Independent assessment — AGREE with CEILING=3 and with the `:=` form; DISAGREE with the "accept" half of the residual, and record NEW INFORMATION the plans do not carry.**

*New information #1 — R-4 is **bounded** in the crash shape.* The phase's R-4 says the defer counter *"advances only on launcher boots"* and that a launcher which stays up while the child crash-loops never advances it (`launcher.sh:1507-1560`). That is true for the counter, but the **launcher does not stay up** in the sustained-crash shape: `budget_tick` counts crashes within a 10-minute window (`launcher.sh:57-59` — `BUDGET_MAX_CRASHES=5`, `BUDGET_WINDOW_S=600`; the tick at `launcher.sh:1409-1431`), and on `action = "abort"` the launcher logs *"HALT: burst budget exceeded … exiting 1, staying down"* and exits 1 (`launcher.sh:1597-1602`). The unit's `Restart=on-failure` + `RestartSec=10` (`scripts/systemd/ensemble-daemon.service`) then **re-boots the launcher**, and `_journal_sweep` runs again at `launcher.sh:1662`. So in the crash shape the defer counter **does** advance — **[PLANNER r3] roughly once per ~3.5 minutes (a ~200 s dying boot + the unit's `RestartSec=10`)** — and CEILING=3 ⇒ **[PLANNER r3] the rollback fires in ~10–15 min**. **[PLANNER r3] Correction (truthfulness, carried doc-hygiene):** the earlier text here said *"roughly once per ~10 minutes"* and *"the rollback fires in ~30 min"*; the ~10 min figure conflated the **burst-budget window** (`BUDGET_WINDOW_S=600` — the window over which crashes are *counted*) with the **launcher-boot cadence** (the interval between boots, which is the dying-boot duration plus `RestartSec=10`). In a `RestartSec=10` + ~200 s dying-boot environment each boot is ≈3.5 min ⇒ three qualifying observations ≈ **10–11 min**, i.e. **~10–15 min** end to end. The same corrected figure is now carried in `phase2-plan.md` **matrix row 4** and `architecture-recommendation.md` **§6(iii)** (whose "≈30–40 min" estimate is corrected by the same reasoning). **R-4 is therefore materially less severe than either plan states**, and option (b)'s run-loop re-sweep hook is **unnecessary for the crash shape**.

*New information #2 — R-4 is **unbounded** in two shapes the plans do not name at all.* (i) **Tempfail (exit 75):** `budget_tick` returns `exempt` for exit 75 (`launcher.sh:1412-1414`) ⇒ a daemon that boots into a persistent tempfail (the unit's own comment names *"PG unreachable?"*) **never** accumulates the crash budget ⇒ the launcher never aborts ⇒ the sweep never re-runs ⇒ the defer counter never advances ⇒ the broken release stays on the live symlink indefinitely. (ii) **Clean exit 0:** the `clean` verdict makes the launcher *"exiting 0, not looping"* (`launcher.sh:1565-1569`) ⇒ `Restart=on-failure` does **not** restart a success ⇒ no further launcher boots ⇒ same outcome. Neither shape is covered by the defer ceiling, because the ceiling counts **launcher boots** and these shapes produce **none**.

*What this changes.* The residual is not *"the defer counter advances too slowly"* — it is *"two child-failure shapes produce no launcher boot at all"*. That is a **different** closure than option (b) (a run-loop re-sweep hook would, in fact, close both shapes, since it would re-sweep per child cycle rather than per launcher boot). So option (b) — which the phase defers — is *more* load-bearing than the phase believes, and the reason to defer it (cost) may be the wrong reason.

*Recommendation to the gate.* Keep **CEILING=3** and the `:=` form (both right). On the residual, the honest options are: **(i)** accept, and record the two named shapes as bounded-by-operator (an operator's `systemctl restart` produces the launcher boot that advances the counter) — the phase's option 2b, **with the two shapes written down**; or **(ii)** commission the run-loop re-sweep hook (option b) as part of this lane rather than a follow-up, because it is the only mechanism that covers both shapes. I do **not** recommend raising the ceiling to 5 (option c) — it lengthens the unbounded window without touching it.

**(d-r2) [PLANNER r2] — THE LEADER'S ADJUDICATION OF THIS QUESTION (2026-11-02, binding).** The plan council REJECTed the plan (2/2 converged, **scoped to Phase 2**) on the ground that the defer-ceiling mechanism, as amended, **never rolled back** in the canonical never-green shape (the daemon never serves ⇒ every launcher observation is absent ⇒ the pre-r2 eligibility predicate — positive-unhealthy or `rollback_requested` only — was never satisfied). The leader ruled:

> **OQ-C-06 — the absent-observation disposition = (a) BOUNDED ABSENT-CEILING.** Rollback becomes eligible after **≥3 absent observations AND owner-dead AND intent_flip AND never-green-observed**. Option (b) (any-absent ⇒ rollback) is **REFUSED** — it would rewrite an owner-ratified principle, and the standing COMPOSITION REQUIREMENT decides this question, not the plan author's preference.

**What this does to the two shapes named in (d).** The tempfail-75 and clean-exit-0 shapes were the reason (d) argued the residual was **unbounded**. **That argument is now partly superseded:** the T15 run-loop hook (AM-P2-7) is **in** this lane, and the absent-ceiling (AM-P2-10) gives the hook's post-child-exit observation a *bounded* route to the rollback — **subject to the ruled predicate**, and **excluding tempfail-class (exit 75) child exits**, because a persistent tempfail is normally *"PG unreachable?"* — an environmental failure the house burst budget **deliberately exempts** (`launcher.sh:1412-1414`), and which must not be converted into the rollback of a healthy release (that is tonight's punishment class, and one more wrongful rollback at live `rollback_window_count` **2/3** would arm the 24 h promote halt).

**What this does NOT change (settled, carried as-is).** The **premise** of (d) is confirmed **TRUE** by both councilors' independent greps: **there is no 6 h promote-txn `in_flight` reaper in-tree.** ⇒ **T15 + the operator runbook + the disclosed residual stand.** The residual is now narrower and precisely stated:

| Shape | r2 status |
|---|---|
| **never-green, daemon crash-looping** | **CLOSED** — the bounded absent-ceiling (AM-P2-10) terminates it in the existing chain; the daemon-side closer (path 2) accelerates it |
| **never-green, daemon silent** | **CLOSED** — same mechanism (the ABSENT class) |
| **tempfail-75 (PG unreachable)** | **OPERATOR-BOUNDED RESIDUAL** — the exit-75 exclusion means the hook does not count it; the runbook's terminal backstop (`systemctl restart ensemble-main`, then manual `rollback.sh`) remains the escape. **Disclosed, not closed by this lane.** |
| **clean-exit-0** | **OPERATOR-BOUNDED RESIDUAL** — one hook observation after the clean exit; no further launcher boot ⇒ no further observation. The same runbook backstop. **Disclosed.** |

**(e) Gate.** **LEADER** (threshold + accepting a residual) with **ARCHITECT** (the shape of the closure). *If (i) accept:* the two shapes (tempfail-75, clean-exit-0) must appear in the plan's residual list and in the operator runbook; the never-rollback window is operator-bounded, not system-bounded. *If (ii) commission the hook:* `run_loop` gains a per-child-cycle re-sweep seam, which is a **new launcher-loop surface** and needs its own pin (and touches the `tests/test_launcher.sh:831-848` 8b-r1 source pin's scope — `launcher.sh:1507-1560` is inside `run_loop`, **outside** the pin's `^_journal_sweep\(\)` extraction, so the 8b-r1 pin is not at risk; but the new seam is new surface either way).

---

#### `OQ-C-07` — (f) Rollback EXECUTION for daemon-side unhealthy verdicts

**Cross-ref:** Phase-2 `OQ-2`; Phase-2 §D.5.

**(a) Question.** The daemon can *decide* "the target is not serving", but it must not be the one to repoint the symlink and stop itself mid-serve. How is the daemon's NOT-GREEN verdict executed as a rollback?

**(b) Options.**
1. **`spawn_executor`-style transient unit** (`daemon/tools/upgrade_journal.py:3044-3113`) — systemd transient unit / `start_new_session` branch, `RuntimeMaxSec` backstop, env via `executor_env`; today called only from `daemon/manager.py:5809`.
2. **Journal disposition → next launcher boot executes it** — the daemon journals the verdict + a rollback *request*; the launcher's existing sweep-rollback chain (`launcher.sh:1209-1242`) executes it at the next boot.
3. **Operator runbook** — a human runs `rollback.sh`.

**(c) Owning phase's recommendation.** **Option 2 primary**, Option 3 as the documented escalation, Option 1 **out of scope** for this phase. Rationale: zero new execution surface; the launcher executes at the *safe* point (pre-daemon, `launcher.sh:1661` vs `:1525`); no new nonce/F2 semantics (a rollback is not a promote); the composition requirement stays provable with the existing pins plus a small set of new ones.

**(d) Independent assessment — AGREE with Option 2 as primary, with three amendments the architect should pressure-test.**

*Amendment 1 — Option 2 and Option 2's own "next launcher boot" trigger are the SAME trigger that R-4 says may never come (see `OQ-C-06`).* Option 2's latency is *"next launcher boot"*, and the shapes in which the launcher never boots again (tempfail-75, clean-exit-0) are exactly the shapes in which the daemon-side closer is the *only* thing running — and in those shapes the daemon-side NOT-GREEN verdict would journal a disposition that **nothing executes**. So Option 2's effectiveness is coupled to the same residual R-4 describes. This is not an argument against Option 2; it is an argument that **Option 2 and the R-4 closure must be decided together** (one gate decision, two questions).

*Amendment 2 — Option 2 needs a concrete handoff contract, and the plan does not specify one.* "The launcher executes it at the next boot" requires: a journal field or event the launcher reads; a check in the launcher's flipped branch that *precedes* the defer path (otherwise the launcher defers on a NOT-GREEN txn and never executes the disposition); and a definition of what the launcher does with the request (the existing chain, re-pointing to `prev`). The plan's T8/T11/T12 name the events but the **hand-off seam between the daemon's request and the launcher's execution is not a numbered task**. The architect should require that seam as an explicit task + pin before accepting D.5, otherwise the composition requirement's second direction (never-green still rolls back) is satisfied on the *launcher's* axis but not on the *daemon-closer's* axis.

*Amendment 3 — Option 1's rejection is right, but for a stronger reason than the plan gives.* The plan says Option 1 would make the daemon orchestrate its own termination. The sharper reason: Option 1 would introduce a **second rollback executor** with its own `RuntimeMaxSec` derivation (`daemon/tools/upgrade_journal.py:2556-2568`), its own nonce/F2-adjacent semantics, and its own argv-construction policy — while the launcher chain is *already* the reviewed, idempotent, flip-first executor (the ADR-024 ordering comment at `launcher.sh:1131-1136` explicitly prefers flip-first because *"journal-first would strand the env on the orphaned flip"*). Option 1 would put the rollback on the **journal-first** side of that documented preference, for no gain. Recommend the architect record that reasoning so the rejection is not revisited as an "efficiency" question later.

**(e) Gate.** **ARCHITECT** (this is the commission's explicitly-named design pressure-test). *If Option 1 is revived:* a new execution surface, a new policy for who decides, and a documented deviation from the flip-first ordering — all requiring their own plan. *If Option 2 is confirmed:* the daemon→launcher handoff seam must become a numbered task + pin, and it inherits the `OQ-C-06` residual. *If Option 3 is elevated:* the composition requirement is broken (manual-only never-green) — **not acceptable** under the owner ruling.

---

#### `OQ-C-08` — Event naming

**Cross-ref:** Phase-2 `OQ-3`.

**(a) Question.** Vocabulary for the three new journal events.

**(b) Options.** (a) `sweep_deferred` / `serving_green_commit` / `quarantine_cleared_serving_green`; (b) `sweep_commit` / `sweep_defer` / `quarantine_cleared_serving_green`; (c) prefixed by side.

**(c) Owning phase's recommendation.** **(a)** — `serving_green_commit` is **side-neutral** (launcher, daemon and `lib.sh` all write it) and `quarantine_cleared_serving_green` is **owner-ratified verbatim** (must not be renamed). `sweep_deferred` matches the launcher's existing `sweep` / `sweep_rollback` vocabulary.

**(d) Independent assessment — AGREE with (a).** The load-bearing argument is the owner-ratified name: the KV ruling names `quarantine_cleared_serving_green` explicitly, so renaming it would break the ruling's own traceability. The side-neutral `serving_green_commit` matters because **three writers** produce it (launcher `launcher.sh:975+`, daemon `upgrade_journal_sweep.py`, shell `lib.sh` adopt path) and a side prefix would fragment the vocabulary into three near-duplicates — and the *shared* property (all three write the same event) is exactly what the shared pin (P3-PIN-12 / Phase 2's P-8..P-12 family) proves. Option (c) would also make the `_TERMINAL_EVENTS` membership (`daemon/tools/upgrade_journal.py:1874-1884`) side-specific for what is semantically one outcome.

**(e) Gate.** **ARCHITECT** (confirm; low stakes, but the names become a wire vocabulary). *If changed:* every pin that greps for the event name changes, and the owner-ratified name must be preserved regardless.

---

#### `OQ-C-09` — `adopt_stale_txn` scope

**Cross-ref:** Phase-2 `OQ-4`; Phase-2 §D.4; task T11.

**(a) Question.** Is the promote-entry mirror of the same wrongful-rollback shape in this phase, deferred, or dropped?

**(b) Options.** (a) same phase, separate task (T11); (b) deferred to a follow-up; (c) dropped.

**(c) Owning phase's recommendation.** **(a)** — it is the same shape on the decision-maker an operator is most likely to walk next (re-promote after tonight's refusal); `lib.sh` is not purity-constrained so it can use the mature classified probe (`lib.sh:3841-3860`); cost = one scoped task + two pins.

**(d) Independent assessment — AGREE with (a).** The ruling is a *disposition principle*, and a principle that holds on two of three decision-makers is not a principle. Concretely: after tonight's sequence, the operator's next act **is** a re-promote, which walks `adopt_stale_txn` — the path where, untreated, the *same* wrongful rollback + quarantine would recur. Deferring it means the fix lands but the most likely next action still reproduces the incident. The cost is genuinely bounded: `lib.sh` already has the classified probe and the commit-close mutation order is the same as the launcher's (`launcher.sh:1108-1127`), so T11 is mostly plumbing. **[PLANNER r5 → BLOCKER 2 — THE MUTATION-ORDER CLAIM IS SUPERSEDED (append-only; the text above is retained for the audit trail).]** *"the commit-close mutation order is the same as the launcher's (`launcher.sh:1108-1127`)"* is **false in the unsafe direction**: `launcher.sh:1108-1127` is the **OLD close-before-history** order (verified in-tree at base `941cb6321`: `:1110` `current` → `:1118` `previous` → `:1120` `in_flight`→null → `:1122` history append — the txn closes **before** the `serving_green_commit` row exists). Mirroring it would defeat **AM-P2-10.3's** journal check (the terminal event must exist **while** the txn is still open) and would red **P-19 / P3-PIN-23**. ⇒ the correct order is **AM-P2-1's history-before-close** order: `current` → `previous` → history append `serving_green_commit` (with `quarantine_cleared_serving_green` **FIRST** when the target is quarantined) → `in_flight`→null → `quarantined[]` rewrite (opportunistic) — **the same order the launcher uses after the r4 fold**, so the two sides *do* agree; they simply do **not** agree on `launcher.sh:1108-1127`. This is the **fifth** site of this claim in the plan set (the register's **D.2** row, `phase2-plan.md` §D.1 / §D.2 / `T2`, and `decisions.md` §4.5 direction ① were the other four). The *"T11 is mostly plumbing"* conclusion **stands** — with the order corrected. One caution the architect should carry into the pin design: `lib.sh`'s adopt path **counts rollbacks it genuinely performs** (Phase 2 §D.4 note), so the adopt path's NOT-GREEN branch must keep the counter/cooldown semantics byte-identical — the P3-PIN-12 family (never-green packs) is the guard.

**(e) Gate.** **ARCHITECT** (confirm). *If deferred:* the lane leaves a known second instance of tonight's shape on the operator's most likely path; it must then be recorded as an open follow-up with a named owner, not dropped silently.

---

#### `OQ-C-10` — Alert-map entries for the new events

**Cross-ref:** Phase-2 `OQ-5`; Phase-2 §D.7.

**(a) Question.** Which new events alert?

**(b) Options.** (a) none for all three; (b) `quarantine_cleared_serving_green` → a new kind; `serving_unhealthy` → `upgrade_auto_rollback`; (c) all three alerted.

**(c) Owning phase's recommendation.** **(b)** — `serving_green_commit` is a *good* outcome and must stay silent (the policy is explicit: *"commit … emit NOTHING"*, `daemon/tools/upgrade_journal.py:590-596`); but `quarantine_cleared_serving_green` **reverses** an alert the operator already saw (`quarantine` → `upgrade_auto_rollback`, `:649`) — silence would leave the operator believing the release is still condemned; `serving_unhealthy` announces a rollback the launcher will execute ⇒ reuse `upgrade_auto_rollback`.

**(d) Independent assessment — AGREE with (b), with one refinement.** The reasoning is sound and traceable to the house's own alert policy. The refinement: `serving_unhealthy` is written by the **daemon** and is a *request*, not an execution (Phase 2 §D.1: the daemon *"does not repoint the symlink, does not stop itself, and does not count a rollback"*). Announcing it as `upgrade_auto_rollback` — the same kind as the launcher's *executed* rollback (`launcher.sh:1225-1226`) — means an operator watching the alert stream sees the **same kind twice** for one rollback (once as a request, once as an execution) and cannot tell which is which. The phase's own D.7 rationale says *"it announces a rollback that the launcher will execute"* — which is precisely the duplication. **Recommendation:** keep `serving_unhealthy` on `upgrade_auto_rollback` (so the operator's existing watch catches it) but ensure the **detail payload names the state** (request vs executed), so the two are distinguishable in the stream without inventing a new alert kind. This is a detail-level adjustment inside the phase's own chosen option (b), not a new option.

**(e) Gate.** **ARCHITECT** (confirm). *If changed to (a):* the operator never learns that a quarantine was stood down — the exact confusion the ruling is meant to remove.

---

#### `OQ-C-11` — Does the serving-green path require `intent_flip` evidence?

**Cross-ref:** Phase-2 `OQ-6`; Phase-2 matrix row 6; Phase-2 §D.2 placement ladder.

**(a) Question.** Is a positive serving verdict self-authenticating, or must it be joined by `intent_flip` evidence for the same target?

**(b) Options.** (a) **YES** — serving-green alone is insufficient; (b) NO — a positive serving verdict is self-authenticating.

**(c) Owning phase's recommendation.** **(a) — require `intent_flip`.** A positive health read proves the daemon is *running* the target; it does **not** prove the flip was operator-authorized. The existing commit path already sets this evidence standard (`launcher.sh:1068-1079`, reader `launcher.sh:518-536`). The cost is matrix row 6 (an unauthenticated flip defers → ceiling → rollback) — *"the conservative outcome, and the correct one."*

**(d) Independent assessment — AGREE with (a).** This is the security-relevant hinge of the whole design, and the phase is right on the reasoning and right on the *shape* of the cost.

*Why it is right.* The reader is a **conjunction over a history array** — `pattern='*"event":"intent_flip"*target '"$target"'*'` (`launcher.sh:518-536`). A serving-green verdict proves the **target** is healthy; `intent_flip` proves **this target** was flipped with operator intent. Without the conjunction, anyone able to write a journal history entry can make the sweep "commit" an unauthorised flip onto the live symlink — the journal is a plain file, and the launcher reads it with no signature. The serving check answers *"is it up?"*, not *"was it supposed to be?"* — orthogonal facts, and only one of them is about authorisation.

*What the phase's own matrix row 6 does — and does not — say.* Row 6 states that a stale flipped txn with serving-green but **no** `intent_flip` defers and eventually rolls back. That is the conservative outcome *for the flip*. But note the interaction with `D.2`'s ladder: the serving-green branch sits **first**, ahead of the existing `boot_sweep_commit_and_continue` gate (`launcher.sh:1068-1079`), which *itself* requires `intent_flip`. So without the `intent_flip` conjunct on the serving-green branch, the phase would have **weakened** the existing gate — the new branch would fire on a serving-green txn that the old gate would have refused (the old gate additionally requires `prev.rollback_safe=false`, i.e. an *operator-declared incompatibility*). The `intent_flip` conjunct restores parity with the old standard; the `rollback_safe` conjunct is deliberately *not* required, because the ruling's evidence (serving health) is strictly stronger than an operator declaration. **That is the correct asymmetry**, and it should be stated as such in the architect review: *"serving-green replaces `prev.rollback_safe=false`; it does not replace `intent_flip`."*

*One gap to close.* The phase requires `intent_flip` on the launcher branch (A6's ladder, step 1). The **daemon-side closer** (D.1) and the **`adopt_stale_txn`** branch (D.4 / T11) must carry the **same** conjunct — the daemon's version of the evidence read does not exist today, and `lib.sh` has its own history reader (the plan notes *"lib.sh has its own history reader"* without naming the site). The architect should require the `intent_flip` requirement to be **named explicitly in D.1's predicates and in T11**, not left to the implementation, because it is the one predicate that must be identical across all three decision-makers or the ruling is enforced unevenly.

**(e) Gate.** **ARCHITECT** + **LEADER** (the LEADER because the *cost* of (a) is a policy choice: an unauthenticated flip will be rolled back). *If (b):* a forged journal entry can drive a commit-close onto an unauthorised release — the serving-green path becomes an authorisation bypass. *If (a) is confirmed:* the conjunct must be added to D.1's predicate table (P-number) and to T11, and pinned on all three axes.

---

#### `OQ-C-12` — bash 3.2 / Darwin support for the launcher probe

**Cross-ref:** Phase-2 `OQ-7`; Phase-2 §D.2 A4; Phase-2 risk R-11.

**(a) Question.** Is the `/dev/tcp` + `read -t` probe supported on bash 3.2 (macOS) in this phase, or is Darwin explicitly waived?

**(b) Options.** (a) support both (pin bash 3.2); (b) **Linux-only for this phase, with the waiver recorded**; (c) use `_js_run_bounded` + a nested bash instead of `read -t`.

**(c) Owning phase's recommendation.** **(b)** — the launcher carries Darwin arms (`launcher.sh:247-263`, `:759-770`), so a silent Linux-only probe is a real (if small) portability regression; but the `read -t`-on-a-socket-fd behaviour under bash 3.2 is **unverified in this tree**, and option (c) reintroduces the hang-class the `_js_run_bounded` design was built to avoid. Implement with `read -t` + the `<&3` form (A4), pin Linux, and file a **small follow-up** to verify/port to bash 3.2 rather than guessing now.

**(d) Independent assessment — AGREE with (b), with one scoping correction.** The phase's reasoning is correct and the risk calculus is right: a probe that hangs on the boot path is strictly worse than a probe that is absent on Darwin (the absent case degrades to **defer**, never to a wrong rollback — because "not positively green ⇒ DEFER" (A6) is the fail-safe direction). Option (c) is correctly rejected.

The correction: the waiver must be **narrower than "Linux-only"**, and the phase's own D.2 A3/A6 machinery already provides the safe degradation. Concretely, on bash 3.2 the probe should **not** merely be unpinned — it should **degrade deterministically** to NOT-GREEN (⇒ defer) rather than risk an unbounded read. That means the implementer must **bound the probe in a way that is bash-3.2-safe by construction** (e.g. the `read -t` timeout form plus the total-budget bound), and the waiver should be scoped to *"the probe's **behavioural** behaviour under bash 3.2 is unverified; on an unverified platform the probe returns NOT-GREEN (defer) rather than attempting the read"*. The phase's A4 already says *"verify `read -t` timeout behaviour under bash 3.2 before relying on it"* — this register upgrades that from a note into the **acceptance condition** for the Darwin arm: *unverified ⇒ NOT-GREEN ⇒ defer, never a read that could hang.*

**(e) Gate.** **ARCHITECT** (confirm the waiver's scope). *If (a) is chosen instead:* a bash-3.2 CI leg is needed, which is a host-provisioning cost the lane does not carry. *If (b) is confirmed:* the Darwin-arm degradation must be an explicit implementation requirement, and the waiver must be disclosed in the release notes (as the phase's D.7/OQ-5 alerting already discloses behaviour changes).

---

#### `OQ-C-13` — Defer-counter mechanism

**Cross-ref:** Phase-2 `OQ-8`; Phase-2 §D.6.

**(a) Question.** How is the deferral count carried?

**(b) Options.** (a) history-count keyed by `run_id`; (b) a new journal field written by the daemon + read by the launcher; (c) a field inside `in_flight` rewritten wholesale by the launcher.

**(c) Owning phase's recommendation.** **(a) — history-count.** The launcher **cannot create a journal field** (`_js_journal_update` returns 1 on an absent field — `launcher.sh:387-393`, verified), and the launcher sweeps **before** the daemon exists (`launcher.sh:1661` vs `:1525`), so the daemon cannot pre-seed one. A history-count reuses the proven conjunction-reader technique (`launcher.sh:518-536`), needs no new writer semantics, and over-counts in the **safe** direction. **[PLANNER r4 → BLOCKER 4 — SUPERSEDED (append-only; the text above is retained for the audit trail).]** The *recommendation* is **AM-P2-5's launcher-local counter file** `<INSTALL_DIR>/.sweep-defer-<run_id>` — **not** a history count. The reasoning that forced "no new journal field" **stands** (it is a real capability boundary, verified below); what changes is that a **local file** satisfies it without the history's cost and hazard profile. The *"over-counts in the safe direction"* clause is **INVERTED** — see (d).

**(d) Independent assessment — AGREE with (a); the constraint is not a preference but a hard capability boundary.** Verified independently: `_js_journal_update` (`launcher.sh:387-393`) returns 1 when the field literal is absent, and the daemon's `journal_update_field` raises `KeyError` on an absent field (*"new fields must be introduced via ensure_extensions"*, `daemon/tools/upgrade_journal.py:375-389`). So (b) is impossible from the launcher side **and** requires a daemon-side `ensure_extensions` addition (a schema-extension surface) — while the daemon sweeps only *after* the launcher. (c) would rewrite the whole `in_flight` object — a wholesale-overwrite hazard on the one field the promote machinery owns. (a) is the only mechanism the constraints permit.

One property worth stating explicitly for the pin design: the history-count **over-counts** in the safe direction because a cross-entry-boundary match accelerates the backstop (`launcher.sh:1144-1147` documents the same doctrine for the rollback counter). The pin must therefore assert *"≤ CEILING deferrals before the rollback fires"* and **not** assert an exact deferral count — a pin that expects exactly 2 deferrals would be red under a legitimate over-count. **[PLANNER r4 → BLOCKER 4 — the "over-count is the safe direction" clause is INVERTED (append-only; the text above is retained for the audit trail).]** The history-count was **replaced** (AM-P2-5) by the launcher-local counter file, and the doctrine that justified it does **not** survive: the counter now gates the **rollback itself**, so an over-count fires a **wrongful rollback** — at the live `rollback_window_count` **2/3**, one false rollback **arms the 24 h promote halt** (the architect's gap #2). ⇒ the safe direction for the *count* is **under-count**, and the protection against a wrongful rollback is carried by the **observation conjuncts** of the ceiling predicate (owner-dead ∧ `intent_flip` ∧ **never-green-observed**, AM-P2-10.2) plus the sticky-green flag and the journal-history check (AM-P2-10.3) — **not** by over-counting. The pin-design advice survives in amended form: the pin asserts *"≤ CEILING deferrals before the rollback fires"* (never an exact count), and now also asserts the **three conjuncts** individually (P3-PIN-20).

**(e) Gate.** **ARCHITECT** (confirm). *If changed:* the constraints above make (b)/(c) expensive-to-impossible; the phase's choice is effectively forced.

---

#### `OQ-C-14` — `_TERMINAL_EVENTS` membership + pending_op closure

**Cross-ref:** Phase-2 `OQ-9`; Phase-2 §D.7; Phase-2 risk R-10.

**(a) Question.** Which new events are terminal for the txn?

**(b) Options.** (a) `serving_green_commit` ∈ terminal; `sweep_deferred` ∉; (b) all three ∉.

**(c) Owning phase's recommendation.** **(a)** — a commit-close **is** a terminal outcome; the existing precedent (`boot_sweep_commit_and_continue` ∈ `_TERMINAL_EVENTS`, `daemon/tools/upgrade_journal.py:1883`) says so. A deferral closes nothing — if it were terminal, a deferral would close a pending_op, which is exactly wrong.

**(d) Independent assessment — AGREE with (a).** The distinction the phase draws is the load-bearing one: `serving_green_commit` **ends** the txn (it clears `in_flight` and sets `current`/`previous`), while `sweep_deferred` **holds** it (`in_flight` stays open — that is precisely what the defer means). The R-10 hazard is real and asymmetric: a deferral wrongly marked terminal would let a deferral close a pending_op, which would make the never-green backstop unreachable. One note for the pin: the terminal-set change must be pinned **against the deferral** specifically (the T12 pin named in Phase 2's risk R-10), because the composition requirement's second direction depends on the deferral leaving the txn open.

**(e) Gate.** **ARCHITECT** (confirm). *If changed to (b):* a serving-green commit would leave the txn open in the daemon's view — the commit-close would not terminate the pending_op, and the next sweep tick could re-judge it.

---

#### `OQ-C-15` — Who wins when the launcher sweep and the daemon closer race the same txn?

**Cross-ref:** Phase-2 `OQ-10`; Phase-2 risk R-6.

**(a) Question.** Launcher sweep and daemon closer both act on the same stale flipped txn. Who wins?

**(b) Options.** (a) whoever takes the journal lock first, each revalidating under it; (b) the daemon always wins; (c) the launcher always wins.

**(c) Owning phase's recommendation.** **(a) — lock + revalidate under it, on both sides.** The launcher already serializes on the D5 lock and revalidates under it (`launcher.sh:921-944`); the daemon side must adopt the same discipline (take `lock_acquire`, `daemon/tools/upgrade_journal.py:826`, re-read, re-check) — mandatory in T8, pinned by P-12. The verdicts are compatible: both write the same events and the same mutation order, so *"who wins"* changes only the journal author, not the outcome.

**(d) Independent assessment — AGREE with (a).** The phase's core argument — *"the txn object is the identity, and any field change means the owner acted"* — is the right invariant, and it is what makes the race benign. Two details the architect should pin:

1. **The daemon's lock discipline must mirror the launcher's *revalidation*, not just its lock acquisition.** The launcher's revalidate-under-lock is what prevents the double-mutation; taking the lock alone does not. Phase 2's T8 wording says *"takes the journal lock and re-reads the txn under it"* — that is the right requirement, and the pin (P-12) must assert **re-read + predicate re-check**, not merely "lock was acquired".
2. **The daemon's journal writers are atomic *per write*, not transactional *across steps*.** Verified: `journal_write` is an atomic whole-document write, and `journal_update_field`/`journal_history_append` are separate calls (`daemon/tools/upgrade_journal.py:332-407`); the research census states *"per-write atomic NOT cross-step transactional"* (KV `research_C_daemon`). The daemon's GREEN path therefore performs **five** separate mutations (`current`, `previous`, `in_flight`, history, quarantine array). If the daemon crashes mid-sequence, the journal is left partially mutated. The launcher has the same exposure (its mutations are separate atomic temp+mv writes, `launcher.sh:1217-1230`), and it is *idempotent* by construction — which is why the phase's ordering (the `previous` write must land between `current` and `in_flight`) matters. **The pin must therefore assert the journal state after an interrupted mutation** (a fault-injection leg), not only the happy path. This is an addition to the pin set, not a change to the design.

**(e) Gate.** **ARCHITECT** (confirm + the two pin amendments). *If changed to (b)/(c):* one decision-maker silently pre-empts the other; since both write the same events and the same mutation order, (b)/(c) buy nothing and cost a permanent asymmetry.

---

### 3.3 Phase 3 questions

---

#### `OQ-C-16` — Which cooldown fix form?

**Cross-ref:** Phase-3 `P3-Q1`; **identical to Phase 3's own decision `P3-D1`** (a register duplication — see §2.5 Tension #3).

**(a) Question.** Option A (epoch-argument two-arm `uname` dispatch) vs Option B (mirror `lib.sh:2099-2107`'s relative form).

**(b) Options.** (a) Option A; (b) Option B.

**(c) Owning phase's recommendation.** **Option A** — preserves the working BSD arm byte-for-byte, computes the target instant once, avoids introducing `lib.sh`'s `date(1)` dialect into the launcher. *"If the leader prefers codebase-wide canonical form, take Option B — but then P3-D2 (property-assert, not string-assert) still holds, and the static no-bare-date pin must be re-derived."*

**(d) Independent assessment — AGREE with Option A; the phase's comparison table is correct and I verified the twin.** `lib.sh:2099-2107` is exactly the dispatch the plan describes (verified: `case "$(uname -s)"` at `:2099`, Darwin arm `:2100-2102`, Linux arm `:2103-2104`, fail-closed `*)` at `:2105-2107` — and the `*)` arm writes `_now_iso` after a loud stderr *"unrecognized platform … refusing (fail-closed; BSD or Linux required)"*). Option A's advantages are real: one `_now()` call (`:1333`) ⇒ one instant; the BSD arm stays byte-identical; and the launcher stays internally consistent with its two existing `uname` dispatches (`:247-263`, `:755-770`). Option B's canonical-form benefit is genuinely secondary and, per the phase, would mix a defect fix with a refactor. **However — Option B has one property Option A lacks** that the phase's table does not score: Option B's `*)` arm can be written to **not fabricate a stamp** (which is what `OQ-C-17` needs), while Option A's `*)` arm has the same fabrication hazard. Since the phase's own `P3-D2` requires the pins to assert *property*, both forms remain compatible with P3-D2. **Recommendation:** Option A, with the `*)` arm resolved under `OQ-C-17`.

**(e) Gate.** **LEADER** (the phase explicitly offers the leader the Option B override). *If Option B:* the BSD `date(1)` dialect changes (regression surface on the one platform that works today), and the static no-bare-date pin must be re-derived.

---

#### `OQ-C-17` — What should the unrecognized-platform `*)` arm do?

**Cross-ref:** Phase-3 `P3-Q2`. **Flagged for special weight by the commission.**

**(a) Question.** On a platform that is neither BSD nor GNU, what does the launcher's cooldown arm write?

**(b) Options.**
- **(i)** Mirror `lib.sh:2105-2107` — write `_js_now_iso` (a parseable, **already-expired** stamp ⇒ the anti-flapping window is silently disabled).
- **(ii)** Write **nothing** (leave the field untouched) + a loud stderr + a journal history row.
- **(iii)** Write an **unparseable** marker (the consumer's fail-closed path at `lib.sh:2144-2146` treats it as ACTIVE ⇒ promotes blocked until the next rollback overwrites it).

**(c) Owning phase's recommendation.** **(ii) — do not fabricate a stamp.** Option (i) reproduces exactly the corrupt state that made tonight's incident hard to read (`cooldown_until 22:28:40Z` preceding the `22:29:58Z` sweep row). Option (iii) is fail-closed but self-locking. The residual (the window is genuinely absent on such a platform) is disclosed, and the platform is practically unreachable given the launcher's two other `uname` dispatches already fail closed there.

**(d) Independent assessment — DISAGREE with option (ii) as written; the recommendation does not deliver its own stated outcome. Option (iii), scoped, is the honest form.**

*Why (ii) fails.* Option (ii)'s promise is *"honest: no cooldown was armed"*. But the consumer's behaviour depends on the **pre-existing value of the field**, not on whether this call wrote anything:

```
# scripts/upgrade/lib.sh:2140-2150 (verified)
cd_epoch="$(_json_field "$json" "cooldown_until")"
case "$cd_epoch" in null) return 1 ;; esac
if [ -z "$cd_epoch" ] || ! until_epoch="$(_iso_to_epoch "$cd_epoch")"; then
    _warn "cooldown_until is unparseable ('${cd_epoch:-<absent>}') — treating cooldown as ACTIVE (fail-closed, ADR-005 anti-flapping)"
    return 0
fi
[ "$(_now_epoch)" -lt "$until_epoch" ]
```

Two sub-cases, both bad:

- **The field is ABSENT** (the normal case — no prior rollback ever armed it). `_json_field` returns empty ⇒ `[ -z "$cd_epoch" ]` ⇒ the consumer **warns and returns 0 = ACTIVE** ⇒ the next promote is **refused** (fail-closed). So option (ii) in the absent-field case is **exactly option (iii)'s "self-locking" behaviour**, which the phase explicitly rejected.
- **The field holds a stale, parseable, already-expired stamp** (the case after any prior cooldown has elapsed). "Write nothing" leaves it expired ⇒ the consumer's final comparison `[ now < until ]` is false ⇒ the cooldown reads **NOT active** ⇒ **the anti-flapping window is silently disabled** — *the exact defect the rider closes, surviving the fix*.

So option (ii) is (i) or (iii) depending on history, and is never the "honest" middle the phase intends. **The honest form of "we did not arm a cooldown" is to say so in a way the consumer cannot misread** — which is option (iii) (an explicitly unparseable marker ⇒ the consumer's fail-closed path ⇒ ACTIVE ⇒ the window is armed, conservatively, and the operator sees the loud warning). The phase's own stated objection to (iii) — *"self-locking"* — is a **cost** of fail-closed, not a correctness defect; and the cost is bounded: the next genuine rollback overwrites the field, and an operator can clear it. Compare: option (i) silently disables a protection (the defect class this commission exists to close).

*Second observation — the canonical twin has the same defect.* `lib.sh:2105-2107` — the very form Phase 3 calls "the canonical pinned twin" — writes `_now_iso` on the unrecognized platform, i.e. **option (i)**. So the launcher's recommendation to *not* mirror the twin would leave the codebase with **two different unrecognized-platform behaviours**. That is a legitimate outcome (the launcher is a standalone artifact and is not obliged to match), but it must be **recorded**, not accidental — and the phase's own P3-D3 rationale ("the rider should reduce divergence, not attempt a refactor") is the right frame: divergence is reduced in the *fix*, not in the *fallback policy*.

*Recommendation to the gate.* Choose **(iii), scoped**: on an unrecognized platform the launcher writes an explicitly **unparseable** marker (not `now`), emits the loud stderr, and appends the history row — so the consumer treats the cooldown as **ACTIVE** (fail-closed), the operator sees a loud warning, and the anti-flapping window is *not* silently disabled. Optionally, record the `lib.sh:2105-2107` twin's option-(i) behaviour as a **separate, named** follow-up (it is the ceremony-side twin of the same defect class and is out of this lane's scope) rather than silently diverging.

**(e) Gate.** **ARCHITECT** (the behaviour of an unreachable platform arm is a design question, not a policy one — but it changes what the pin asserts). *If (i):* the anti-flapping window is silently disabled on unknown platforms — the defect class survives. *If (ii) as written:* the outcome is history-dependent and the stated rationale does not hold. *If (iii):* the window is armed conservatively and the operator is warned; the pin asserts *"the consumer reads the value as ACTIVE"*, which is the same property P3-PIN-04 already asserts for the GNU arm.

---

#### `OQ-C-18` — Should the launcher cooldown be de-duplicated against `lib.sh`'s `journal_count_rollback`?

**Cross-ref:** Phase-3 `P3-Q3`.

**(a) Question.** Refactor the two twins into one, or keep the launcher self-contained?

**(b) Options.** (a) launcher-only in this lane (phase's recommendation); (b) de-duplicate now.

**(c) Owning phase's recommendation.** **No — launcher-only in this lane.** The launcher cannot source `lib.sh` (it needs the install dir; `launcher.sh:8-15` fixes the launcher as a standalone sourced unit) and R-08 bars `python3` from the launcher. De-duplication is a separate lane; *"the rider should reduce divergence, not attempt a refactor."*

**(d) Independent assessment — AGREE.** The constraints are structural, not stylistic: the launcher is a **staged standalone artifact** copied into the install dir (`scripts/upgrade/stage.sh:424`) and never sources `lib.sh` (`launcher.sh:8-15`), and R-08 forbids a `python3` dependency. A de-duplication attempt would mean either breaking the launcher's self-containment (a hard constraint) or shipping a shared file into the payload (a payload-shape change, a stage-time change, and a new manifest field — far outside a defect fix). **The phase's framing is right and should be preserved verbatim in the implementation brief**: this lane *reduces* divergence (the launcher gains the same two-arm dispatch the twin already has) without attempting to remove it.

**(e) Gate.** **ARCHITECT** (confirm; the answer is effectively forced by R-08 + the payload shape). *If de-duplicated:* a new payload surface + a stage-time change + a manifest field — a different commission.

---

#### `OQ-C-19` — Pin placement for the rider?

**Cross-ref:** Phase-3 `P3-Q4`; **identical to Phase 3's own decision `P3-D3`** (a register duplication — §2.5 Tension #3).

**(a) Question.** New dedicated pack vs new sections in the boot-sweep file vs `tests/test_launcher.sh`.

**(b) Options.** (a) the **new dedicated pack** `tests/test_launcher_cooldown_arm.sh`; (b) boot-sweep file sections; (c) `tests/test_launcher.sh`.

**(c) Owning phase's recommendation.** **The new dedicated pack**, reusing `tests/upgrade_test_lib.sh` for `_register_fixture` + the F1 fake-HOME fence.

**(d) Independent assessment — AGREE, with the mechanical justification the phase states and one addition.** The mechanical fact is decisive and is independently verifiable: `grep -rn '_js_count_rollback' tests/ scripts/ daemon/` returns **zero** hits — the function has *no pin of any kind today*. A dedicated file makes that fact legible in the PACKS.md scope column and gives the function a home that names it. The addition: the dedicated pack should also **carry the cross-component consumer round-trip** (P3-PIN-04) rather than splitting it into the boot-sweep file — the round-trip's subject is the *cooldown field*, not the sweep, and `tests/upgrade_test_lib.sh`'s F1 fake-HOME fence (`:69-102`) is the safety net for a test that reads and writes a journal-shaped file. The phase's P3-D3/P3-D4 already point this way; this register records it as the operative arrangement.

**(e) Gate.** **ARCHITECT** (confirm; low stakes). *If changed:* the "previously zero references" fact loses its legibility, and the round-trip pin's subject/file mismatch becomes a future reorganisation debt.

---

#### `OQ-C-20` — The C7 section header cites a stale line (`lib.sh:982`)

**Cross-ref:** Phase-3 `P3-Q5`.

**(a) Question.** Correct the stale citation in the file the new pins land in?

**(b) Options.** (a) correct it in this lane (phase's recommendation); (b) leave it.

**(c) Owning phase's recommendation.** **Correct it in this lane** — a one-line doc-only change in the very file the new pins land in, disclosed as a doc-only rider. *"Leaving a stale reference in a file we are extending risks the next worker encoding another stale ref."*

**(d) Independent assessment — AGREE.** The phase's own §4.1.5 "Citation correction recorded" already establishes that the dispatch brief's `lib.sh:1375-1385` reference is wrong and that the real twin dispatch is at **`lib.sh:2099-2107`** (verified: the `case` at `:2099`, the arms at `:2100-2104`, the fail-closed `*)` at `:2105-2107`). A stale citation inside a file this lane extends is exactly the input to the next worker's mistake. The correction is **doc-only**, one line, inside a file the lane already touches.

**This register adds one item of the same class** (it is in scope for the same doc-only rider, and it is recorded here rather than left to memory): **`phase3-plan.md:3` and `phase3-plan.md:22` carry the typo'd run id `r-20261021-212542-8279`**; the correct id is `r-20261101-212542-8279` (§1.2). The phase files are **not** to be modified by this commission — so this correction rides with the implementing lane's doc-only pass, alongside the C7 header fix.

**(e) Gate.** **ARCHITECT** (confirm; mechanical). *If left:* two stale citations persist in files this lane extends.

---

#### `OQ-C-21` — Serving probe for the launcher: `bash /dev/tcp` or `curl`?

**Cross-ref:** Phase-3 `P3-Q6`; **settled by Phase 2's adjudication `A1` and Phase 3's own decision `P3-D9`** — a cross-phase duplicate (§2.5 Tension #3).

**(a) Question.** `/dev/tcp` (pure) or `curl`?

**(b) Options.** (a) `bash /dev/tcp` + the launcher's existing bash JSON field parsing; (b) `curl`.

**(c) Owning phase's recommendation.** **`bash /dev/tcp`** — *"This is no longer a preference"*: Phase 2's purity pin `P-11` forbids executable `curl`/`wget`/`nc`/`lsof`/`python3` in `launcher.sh`; `lib.sh` is not purity-constrained, so `curl` is admissible on the ceremony side.

**(d) Independent assessment — AGREE; the question is **closed**, not open.** It is closed by Phase 2's `A1` (a **binding adjudication on the implementer**, not a recommendation) and Phase 3's `P3-D9` (which records it as binding). The phase itself states it is *"no longer a preference"*. It is retained in this register for traceability, with one clarification the architect should carry: the constraint is **asymmetric by design** — the launcher half and the `lib.sh` half gather *compatible but differently-transported* evidence (raw TCP + bash JSON in the launcher; HTTP via `curl` in `lib.sh`), and **the journal verdict, not the transport, is the shared contract**. A pin must therefore not assume the two halves produce byte-identical evidence — it must assert the same **verdict** for the same serving shape.

**(e) Gate.** **ARCHITECT** (confirm; effectively closed). *If reopened:* Phase 2's `A1` and Phase 3's `P3-D9` must both be reopened together, and the purity pin `P-11` re-derived.

---

#### `OQ-C-22` — Must the rider ship in the SAME release as the Phase-2 closer?

**Cross-ref:** Phase-3 `P3-Q7`; Phase-3 `P3-D8`; Phase-2 §"Sequencing note (shared surface)"; Phase-3 §4.4.2.

**(a) Question.** Same release, or separate?

**(b) Options.** (a) yes, same release (phase's recommendation); (b) separate releases, rider later.

**(c) Owning phase's recommendation.** **Yes.** They sit in the same disposition chain, and the cross-component consumer semantics (`lib.sh:2139-2151`) change in the same promote. Splitting them would leave a window where the launcher arms a cooldown the daemon-side commit-close path does not expect. *(Phase 2's own sequencing note independently requires the rider to land **after** Phase 2 — P3-D8.)*

**(d) Independent assessment — AGREE, and the reasoning is stronger than the phase states.** Phase 2's `D.1` GREEN path explicitly **does not** arm the cooldown (the "NOT done: sweep-rollback, quarantine, counter+cooldown arming" doctrine, `launcher.sh:1034-1036`), so the two features are *complementary rather than conflicting* — the phase's "a window where the launcher arms a cooldown the daemon-side commit-close path does not expect" describes a *coherence* risk, not a live conflict. The stronger argument for the same release is **timing**: the rider's fix is **source-side only** and reaches live **at the next promote** (`scripts/upgrade/stage.sh:424`), so between merge and promote the live launcher still arms a born-expired cooldown — and the *only* thing that covers that interim window is Phase 2's daemon-side closer + the launcher's defer path (Phase 3's `P3-R1`). If the rider ships in a **later** release than the closer, the interim window is not shortened but **re-opened** after the closer is live. **Therefore: same release.** The one caveat the architect should note: the rider's fix also cannot land before Phase 2 in *code* (P3-D8), because the pins would be written against a sweep that no longer exists — so "same release" is compatible with "Phase 2 first (or in-commit)" because the two edits touch **different functions**.

**(e) Gate.** **LEADER** (release packaging). *If split:* the interim window is re-opened after the closer is live, and the `lib.sh:2139-2151` consumer semantics change in a promote where the producer's arming behaviour is still incoherent.

---

#### `OQ-C-23` — Does a serving-green commit-close leave the cooldown untouched?

**Cross-ref:** Phase-3 `P3-Q8`; Phase-2 §D.1 / §D.2 ("NOT done").

**(a) Question.** On a serving-green commit-close, must the rollback counter, the cooldown, and the quarantine array all stay untouched?

**(b) Options.** (a) yes — a serving-green close is not a rollback; (b) arm the cooldown anyway (anti-flapping for the commit).

**(c) Owning phase's recommendation.** **Yes** — it must not increment `rollback_window_count`, must not arm `cooldown_until`, and must not append a quarantine entry. The doctrinal precedent is explicit and in-repo (*"The cap/cooldown are anti-flapping responses to BAD outcomes; a successful-and-declared flip is not a bad outcome"*, `launcher.sh:1034-1036`). P3-PIN-05 asserts all three effects. **Corollary for the rider:** the cooldown arm must remain reachable **only** from the rollback path — `_js_count_rollback` must not be called on the serving-green branch. This is *"a pin obligation on Phase 2, not a Phase-3 design decision."*

**(d) Independent assessment — AGREE; the doctrine is quoted verbatim from the code and the ruling lands on the same side.** This is the cleanest join between the owner ruling and the house doctrine: the ruling says a serving-green release must not be rolled back, and the house doctrine says a good outcome must not be punished with the anti-flap mechanisms. Both point the same way, and Phase 1's D-P1-4 (the hand-back's success claim becomes *verifiable*) plus Phase 2's D.3 (the clearance path) complete the picture. The corollary is the part worth pinning hardest: **P3-PIN-08's C5 re-shape (green ⇒ commit-close, not-green ⇒ the existing chain fires with all four effects) is the single pin that proves both the ruling and the "cooldown only on the rollback path" corollary.** If the C5 re-shape is done as a *deletion* rather than a *parameterisation* (Phase 2 pin `P-3` and P3-PIN-08 both say *"re-shaped, not deleted"*), the commit-close polarity loses its pin. **The re-shape must therefore be a two-branch parameterisation of the existing C5 section, preserving the original assertion text as the not-green branch.**

**(e) Gate.** **ARCHITECT** (confirm; a pin obligation, not a design choice). *If changed to (b):* a serving-green release would arm the anti-flapping window — punishing a good outcome and, in the operator's next-promote experience, refusing their own promote for 10 minutes.

---

#### `OQ-C-24` — Should the lane re-pin the `daemon/constants.py` seal?

**Cross-ref:** Phase-3 `P3-Q9`; Phase-3 risk `P3-R10`; Phase-3 §4.2.3.

**(a) Question.** The `daemon/constants.py` seal tripwire is **already red at base** (baseline `464a2459…` vs the worktree's `5dd9bfa3…`). Phase 2 touches that file. Does the lane re-pin the seal?

**(b) Options.** (a) **no** — record the red as base-red on both legs, route the re-pin to its own lane (phase's recommendation); (b) re-pin in this lane.

**(c) Owning phase's recommendation.** **No — do not re-pin in this lane.** *"Re-pinning would fold pre-existing base debt into the lane's diff and would mask the fact that the seal has been out of date since before this commission."* Record the red as base-red on both legs in the FAILED-digest comparison (the `red-at-base ≠ net-new` rule, `ensure.md:27`); route the seal re-pin to its own small lane.

**(d) Independent assessment — AGREE, strongly.** This is the classic "laundering pre-existing debt" trap and the phase's reasoning is exactly right. Re-pinning the seal in this lane would: (i) make the lane's diff include a change that has **nothing to do with the commission**; (ii) make the lane's FAILED-digest comparison show the tripwire as *green at HEAD*, which would **mask** the fact that the seal was already stale — the very thing the `red-at-base ≠ net-new` rule exists to expose; and (iii) burn the signal that the seal has been out of date independently of this work. The house has a matching precedent (the v0.26.3 lane's *"base-proven seal tripwire"* disclosure), and the practice is to **keep a pre-existing red visible** rather than launder it.

One addition for the gate: the lane's tester report must **name** the base-red explicitly (file, test node id, the two hashes) rather than relying on the FAILED-digest diff to imply it — a named base-red is auditable; an inferred one is a re-derivation burden for the next reader. The pin that proves it is **not** a lane regression is the FAILED-digest comparison's *both-legs* recording, which the phase already requires.

**(e) Gate.** **LEADER** (the call to defer a debt closure). *If (b):* the lane absorbs an unrelated change and the pre-existing seal staleness becomes invisible. *If (a):* the red persists and is attributed to its own (small) follow-up lane.

---

#### `OQ-C-25` — Should the Phase-3 IDs be renumbered into the plan set's convention?

**Cross-ref:** Phase-3 `P3-Q10`; Phase-3 §4.4.1; Phase-1 `A-P1-1`.

**(a) Question.** Flatten the three phases' ID namespaces, or preserve them?

**(b) Options.** (a) keep the `P3-` prefix and carry the cross-reference table; (b) renumber into a flat sequence.

**(c) Owning phase's recommendation.** **Keep the `P3-` prefix in the consolidated set** and record the cross-reference table rather than renumbering — renumbering Phase 1/Phase 2 entries would break their internal references (Phase 2's own text cites `P-1`…`P-12`; the leader's summary cites phase-owned ids). *"If the consolidating worker prefers a single flat sequence, it must **append** new ids and preserve the existing ones."*

**(d) Independent assessment — AGREE; and this register executes (a).** Every id in §2 is preserved with its phase prefix; the cross-reference is carried in the cross-ref columns of §2 and §3, and §5 reproduces Phase 3's §4.4.1 mapping. Nothing in the three phase files was renumbered or edited (verified: `git status --porcelain` in the worktree shows only the untracked planning directory — `?? .agents/shared/planning/promote-handback-halt/`).

**(e) Gate.** **SETTLED** — no gate required. *If a flat sequence is later demanded:* append-only, never renumber.

---

## 4. BINDING-CONSTRAINT COMPLIANCE MATRIX

Each row: **the frozen rule / hard constraint**, **how the plan set honours it**, **where** (plan section + code anchor), and a **compliance verdict**.

### 4.1 Quarantine: "cleared only by re-staging" — **extended, not bypassed**

| Element | Detail |
|---|---|
| **Frozen rule** | `promote_entry_check` refuses a quarantined target: *"version '$target_ver' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version"* (`scripts/upgrade/lib.sh:6559-6562`). The only existing clear path is `journal_quarantine_clear` (`lib.sh:1945-1960`), called from `stage.sh:674` only. |
| **How honoured** | Phase 2 §D.3 amends the rule on **BOTH sides**: (i) the **daemon** rewrites `quarantined[]` (via `uj.journal_update_field`, `daemon/tools/upgrade_journal.py:375-389`) **and** appends the `quarantine_cleared_serving_green` history event (`:392-407`); (ii) the **shell** gains `journal_quarantine_cleared_serving_green <ver>` beside `journal_is_quarantined` (`lib.sh:1963-1969`) and `promote_entry_check` refuses **only if** the version is in `quarantined[]` **AND** no clearance event names it. Task T10 pins: *"without it, the refusal is unchanged (frozen rule intact)."* |
| **Why both sides** | The *state* (the array) and the *durable record* (the event) are different surfaces; and **the gate that refuses is shell-side**, so a daemon-side write alone would not change the behaviour — and vice versa (Phase 2 §D.3; KV `research_C_daemon`: *"daemon-side quarantine clear alone still refused at next promote entry; BOTH sides need the amendment"*). |
| **No bypass** | The clearance event is written **only** by the closer **after** a positive serving-green verdict, which is itself gated on `intent_flip` evidence + staleness + owner-deadness + the health probes. The rule becomes *"cleared by re-staging **or** by an explicit serving-green clearance event"* — no silent edit, no re-staging required. |
| **Pins** | Phase 2 `P-8` (both sides) → consolidated `P3-PIN-12`; Phase 2 `P-7` (the 4-stage sequence) → `P3-PIN-07`; Phase 2 `S-3` (the refusal is lifted **without** weakening the frozen rule). |
| **Verdict** | ✅ **COMPLIANT** — extended with an explicit, evidence-gated, journaled path; the frozen refusal message is byte-unchanged when neither signal is present. |

### 4.2 Rollback cap 3/24h + cooldown semantics

| Element | Detail |
|---|---|
| **Frozen rules** | `SWEEP_ROLLBACK_CAP_24H=3` (`launcher.sh:78`) — *"ENTRY-side only (D-FA4.2): the sweep itself always executes past the cap — refusing the recovery would strand the env on an orphaned flip; reaching 3 arms halt + cooldown for the NEXT entry."* The cap is enforced at promote entry (`lib.sh:6550-6552`). The cooldown is armed by `_js_count_rollback` (`launcher.sh:472-476`) and consumed at `lib.sh:6554-6558` via `journal_cooldown_active` (`lib.sh:2139-2151`), which is **fail-closed** on an unparseable stamp (ADR-005 anti-flapping). |
| **How honoured** | (i) The serving-green path **does not touch** the cap, the counter, or the cooldown — Phase 2 §D.1/§D.2 "**NOT** done: quarantine marking, rollback counter increment, cooldown arming", with the in-repo doctrine quoted verbatim (`launcher.sh:1034-1036`). Pinned by `P3-PIN-05` (all three effects asserted absent) and by the C5 re-shape `P3-PIN-08`. (ii) The **cooldown producer** is repaired by the Phase-3 rider (`P3-D1`): the `*)`/GNU arms make the armed stamp **born-future** instead of born-expired. (iii) The cap's *entry-side* semantics are **unchanged** — `lib.sh:6550-6558` is untouched except for the quarantine line (§4.1). |
| **The live defect** | On GNU, `date -jur` (`launcher.sh:474`) exits non-zero ⇒ the `:475` fallback writes `until = _js_now_iso` = **NOW** ⇒ `cooldown_until` is a parseable, already-expired stamp ⇒ `journal_cooldown_active` returns **not active** ⇒ **the ADR-005 10-minute anti-flapping window is silently disabled on this host** — a *second* instance of tonight's wrongful-rollback family. Corroborated live: the `cooldown_until 22:28:40Z` journal row predates the `22:29:58Z` sweep row. |
| **Pins** | `P3-PIN-01` (born-future property, ±60 s band), `P3-PIN-02` (both arms selected), `P3-PIN-03` (static: zero bare `date -jur` outside the BSD arm), `P3-PIN-04` (**cross-component round-trip** — the value the launcher writes is consumed as ACTIVE; *"the pin that would have caught tonight's live defect"*), `P3-PIN-06` (not-green ⇒ counter++ **and** cooldown armed — the four effects), `P3-PIN-12` (never-green pack family unchanged). |
| **Verdict** | ✅ **COMPLIANT** — the cap's entry-side semantics and the cooldown's *consumer* semantics are byte-unchanged; only the *producer's* defect is fixed, and the fix is proven by the round-trip pin. The unrecognized-platform arm remains open at `OQ-C-17` (the phase's option (ii) does not deliver its stated outcome; this register recommends option (iii)). |

### 4.3 B4 — the txn is left OPEN on halt

| Element | Detail |
|---|---|
| **Frozen rule** | On a halt-for-human, the promote txn is **left open** (B4) — `promote.sh:585`, `:589`. The KV census records the halt path: *"halt caller promote.sh:588-590 (halt-for-human, txn left open B4; halt path does NOT quarantine)"*. |
| **How honoured** | Phase 1 §4.6 lists B4 as **untouched** (*"B4 — txn left open on halt"*), and Phase 1 changes only the verifier's **budget and verdict**, never the hand-back's **disposition**. Phase 2 §matrix row 1 / §"(d) Tonight's 4-stage shape" makes "halt → txn left OPEN, `flipped=true`" the **unchanged** first stage of the acceptance pin — the pin proves the *downstream* behaviour changed while the *halt* shape did not. |
| **Verdict** | ✅ **COMPLIANT** — the incident's opening state is preserved exactly; the fix is entirely in what happens to that open txn afterwards. |

### 4.4 Amendment #1 — no `nohup` fallback; halt-for-human stays for genuinely unconfirmable hand-backs

| Element | Detail |
|---|---|
| **Frozen rules** | *"NO nohup fallback — halting for the caller's B4 policy"* (`promote.sh:587-591`, quoted verbatim in Phase 1 §4.6); the verifier's unconfirmable verdict returns 1 → the caller halts (`lib.sh:4878-4882`, `:4788-4790`); `systemctl start` is **banned as a literal on the success path** (the 7b substring trap, `lib.sh:4795-4800`, pinned at `tests/test_supervision_stop_handback.sh:695`). |
| **How honoured** | Phase 1 §4.6: Amendment #1 **untouched**; the halt-for-human disposition **untouched**; the success-path ban **untouched**. Phase 1's success-path message change (T-P1-8) explicitly carries the constraint: *"no new success-path line contains the banned `systemctl start` literal"*, pinned by leg **L12**. The KV's leader guidance confirms the intent is preserved: *"Amendment #1 intent preserved: halt-for-human stays for genuinely unconfirmable hand-backs; no nohup fallback."* |
| **Note** | Phase 1's Phase-1 §4.2 failure semantics add a **fail-fast** for a wrong-version daemon — a *new* fail-fast class alongside the preserved `MainPID == prestop` fail-fast. That is a **tightening of the failure side only** and does not create a success path, so it does not engage Amendment #1's "never a false success" clause; the amendment's *intent* is strengthened, not weakened (Phase 1 §4.6: *"Strengthened: the serving conjunct makes 'serves the flipped release' **true** rather than assumed"*). |
| **Verdict** | ✅ **COMPLIANT** — no fallback path is added anywhere in the plan set; the halt-for-human disposition is preserved; the banned literal's pin is extended to the new lines. |

### 4.5 Never-green stays armed — **both directions** pinned (the composition requirement)

| Element | Detail |
|---|---|
| **Frozen rule** | The house never-green protection must remain armed: *"never-green MUST still roll back a broken release (that path is untouched and stays armed); serving-green MUST NOT be rolled back."* (KV `design_rulings_owner_ratified`, Ari's COMPOSITION REQUIREMENT.) The existing chain is `launcher.sh:1209-1230` — repoint → quarantine → `current=prev` → count+cooldown → `sweep_rollback` row → `in_flight=null`; the cap/cooldown are *"anti-flapping responses to BAD outcomes"* (`launcher.sh:1131-1136`). |
| **How honoured — direction ① (serving-green ⇒ commit-close)** | Launcher: Phase 2 §D.2's serving-green branch (probe GREEN **AND** `intent_flip` evidence **AND** owner-dead-or-heartbeat-stale — **[PLANNER r4]** the third conjunct is AM-P2-2's owner-liveness predicate, now explicit at the rung) ⇒ `current`=target, `previous`=displaced, **`serving_green_commit` history row, `in_flight`=null**; **no** quarantine, **no** counter, **no** cooldown, **no** `sweep_rollback` row. Daemon: Phase 2 §D.1's GREEN path, same mutation order, plus the quarantine clearance. **[PLANNER r5 → sweep class (a) — THE DAEMON AND ADOPT LEGS NOW CARRY THE AUTHORIZATION CONJUNCT EXPLICITLY (append-only; the text above is retained for the audit trail).]** This cell listed the launcher leg's conjuncts (`intent_flip` ∧ owner-dead-or-heartbeat-stale) but stated the daemon and adopt legs as bare *"same"* — so the **daemon half** of the authorization requirement (OQ-C-11: *"intent_flip is REQUIRED on every serving-green commit, BOTH sides"*) was **invisible at the register's compliance matrix**, which is the cell a compliance reader checks. ⇒ **Daemon:** §D.1's GREEN path **∧ P9** (the `intent_flip` conjunct — **Absent P9 ⇒ no-action-this-tick: no commit, no verdict, no request**), same history-before-close mutation order, plus the quarantine clearance. **Shell (§D.4/T11 adopt):** the same ⇒ **GREEN ∧ `intent_flip` ⇒ commit-close**; the GREEN-without-`intent_flip` arm is the **existing chain** (§D.4 (a-i) — the documented divergence). [DEV-FOLD-2026-11-02 — approver iter003 note 7: a duplicate order-correction sentence stood here ("The order in this cell was already corrected by the r4 fold…"); it is REMOVED — the fuller `[PLANNER r4 → blocker 2, ORDER CORRECTED]` annotation below is the single statement.] **The composition requirement's second direction (direction ②) is unchanged.** Shell: Phase 2 §D.4/T11's adopt path ⇒ same. Pinned by `P3-PIN-05` and Phase 2 `S-1`. **[PLANNER r4 → blocker 2, ORDER CORRECTED]** the ordering stated in this cell — *"`in_flight`=null, `serving_green_commit` history row"* — is the **OLD close-before-history** order and is **SUPERSEDED** (append-only; the text is retained above for the audit trail). The normative order is **AM-P2-1's history-before-close** order: `current` → `previous` → history append `serving_green_commit` (with `quarantine_cleared_serving_green` **FIRST** when the target is quarantined) → `in_flight`→null → `quarantined[]` rewrite. The verified evidence that `launcher.sh:1108-1127` is the **old** order: `:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append. |
| **How honoured — direction ② (serving-unhealthy ⇒ the existing chain still fires)** | Phase 2's NOT-GREEN path journals a verdict + a **disposition** only — *"The daemon does not repoint the symlink, does not stop itself, and does not count a rollback"* — and the launcher executes the **existing** chain byte-identically. Pinned by `P3-PIN-06`, Phase 2 `S-2` (*"byte-identical to today's behavior"*), and `P3-PIN-12` (the never-green pack family `promote_soak_never_green_test.sh` + the fail-fast + grace family). **[PLANNER r2]** **Direction ② has THREE triggers, not one** — and the third is the one the council's REJECT was about. The complete, closed set (Phase 2 `AM-P2-10.5`): **(i) BROKEN** — the launcher obtained an HTTP answer and it was wrong (`/livez` non-200 / unparseable) ⇒ the existing chain fires **immediately**; **(ii) `rollback_requested`** — the daemon's T14 request ⇒ the launcher re-probes ⇒ the existing chain on a NOT-GREEN re-probe; **(iii) the BOUNDED ABSENT-CEILING** — ≥3 qualifying AMBIGUOUS observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed ⇒ the existing chain fires. **Everything else DEFERS.** |
| **[PLANNER r4 → note (l) THE CEREMONY GATE MUST SURFACE THIS]** | The commission's literal Part-1 wording was *"NEVER rollback"*; the **owner-ratified** disposition (OQ-1(a), AM-P2-10) is a **BOUNDED** rollback, not "never". ⇒ the **ceremony notes / operator-facing gate must state the difference explicitly**, or an operator (or a future reader) will read the register and conclude that *any* rollback contradicts the ruling. The reconciled sentence (identical in substance in every doc): **serving-green ⇒ NEVER rolled back (the owner ruling, absolute)**; **serving-unhealthy ⇒ the existing chain still fires, bounded by the absent-ceiling (a leader-ratified bounded ceiling, OQ-C-01/OQ-C-06/AM-P2-10)**. The ceremony gate surfaces: (i) the **bounded** nature of the absent-ceiling (≥3 qualifying AMBIGUOUS observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed — never a first-observation rollback); (ii) the **two paths that fire immediately** (BROKEN, `rollback_requested`); (iii) that a commit-close **never** increments the cap or arms the cooldown (AM-P2-23); and (iv) the live `rollback_window_count` **2/3** figure, so the operator knows one wrongful rollback arms the 24 h promote halt. |
| **The pin that proves both at once** | `P3-PIN-08` — the **C5 re-shape**: today's C5 pins *"unconditional flipped+stale ⇒ rollback"* as INTENDED (`tests/test_boot_sweep_commit_and_continue.sh:279-302`, `:288-296`). Per the ruling it must become **parameterised on serving health** — **C5a** (green ⇒ commit-close, no rollback/quarantine/counter) and **C5b** (not-green ⇒ the existing chain fires with all four effects). Phase 2's `P-3` and Phase 3's `P3-PIN-08` both require the original assertion text to be **re-shaped, not deleted**. **[PLANNER r3 → REV-R-1] SUPERSEDE annotation (append-only — the rows are KEPT, not deleted):** under the r2/r3 taxonomy (Phase 2 `AM-P2-10.1`) the C5b arm is no longer a single *"not-green ⇒ fires"* assertion — it must be **parameterised on the observation class**: **BROKEN** (`/livez` non-200 / unparseable) ⇒ the chain fires **immediately**; **AMBIGUOUS** (no answer / wrong version / `/readyz` not ready) ⇒ **defer** + count toward the bounded absent-ceiling, the chain firing only when the ceiling predicate holds. **Drive instead:** Phase 2 `P-23` / `P-24` / `P-27` and Phase 3 `P3-PIN-20` / `P3-PIN-26` (the ceiling) plus the `livez-non200` fixture mode for the BROKEN leg. A tester building from the un-annotated row would hard-redden against the **correct** r2/r3 implementation. |
| **[PLANNER r3] The restored invariant (F-1)** | **THE INVARIANT, stated normatively and identically in every doc:** **`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.** The daemon-side closer's NOT-GREEN verdict carries the conjunct (AM-P2-24); the boot-grace horizon is the closer's **own** `UPGRADE_CLOSER_BOOT_GRACE_S` (default **300**, **≥ `HANDBACK_VERIFY_BUDGET_S`**), **not** the borrowed 120 s pool-busy constant (`READINESS_GRACE_HORIZON_S`, `daemon/constants.py:165`). ⇒ **degraded-but-grace-held ⇒ DEFER** (no verdict row, no request, the txn stays open). The `GraceTracker` **never changes `ready`** (`daemon/services/readiness.py:757-760`) — **the gate decides to wait; the closer honours the same discipline**; the composite's `grace_active` field (`:407`) is the readable signal. **Direction ② is UNCHANGED**: a genuinely unhealthy serving **outside** grace still produces the verdict + request and the existing chain still fires. Pinned by **`P-35` / `P3-PIN-27`** (the defer leg) **and** `P-6` / `P3-PIN-11a` (the never-green leg, on a **not-grace-held** fixture) — **the two legs cannot be satisfied by the same stub**; scenario `S-14`; risk `R-18`. |
| **Verdict** | ✅ **COMPLIANT** — both directions are pinned, the never-green path is untouched, and the single pre-ruling pin that encoded the old behaviour is explicitly re-shaped rather than removed. **[PLANNER r2]** **VERDICT CORRECTED, then re-established.** The council's REJECT found that this compliance statement was **true in intent but false in mechanism**: as amended, the never-green path was **not armed** in the canonical shape (every observation ambiguous ⇒ the pre-r2 eligibility predicate never satisfied ⇒ **nothing rolled back** — a regression against today's unconditional rollback). **[PLANNER r2]** After the fold, the claim is true **with a named mechanism**: the **bounded absent-ceiling** (AM-P2-10) arms the ambiguous class, and the BROKEN class was always armed. **The never-green path is armed again — boundedly, with three conjuncts, and never on a single ambiguous observation.** No claim was deleted; each was **supplemented with its mechanism**. **[PLANNER r3] Re-established a third time, with the F-1 closure named:** the r3 fold closes the *boundary* defect the r2 fold left open — the daemon closer's NOT-GREEN verdict was reachable **during** a grace, so a healthy slow boot could write a standing `rollback_requested` that path 2 (whose only gate is the re-probe) would later execute. The invariant above closes it **at the writer**; the never-green path itself is **untouched**. |

### 4.6 Launcher purity — R-08

| Element | Detail |
|---|---|
| **Frozen rule** | *"launcher.sh is SELF-CONTAINED — it does NOT source lib.sh, has ZERO python3 dependency, and uses its OWN in-shell [JSON helpers] … DO NOT add a python3 dep here — launcher purity is a hard constraint (R-08)"* (`launcher.sh:1040-1053`); `launcher.sh:8-15` (the standalone-unit contract). |
| **How honoured** | Phase 2 `A1`: **`curl` is not admissible in `launcher.sh`** (zero `curl` occurrences today). `A2`: `bash /dev/tcp` **is** admissible (a bash builtin feature; in-repo precedent `tests/helpers/soak_gate_pack_common.sh:215`, `test/packs/hard_reboot_drill_e2e_test.sh:354`). `A3`: the read is bounded (`read -t`, ≤5 s) so the probe cannot reintroduce the sweep-hang class (`launcher.sh:568-606`). `A4`: bash-3.2-safe shapes only. `A6`: the verdict is tri-state and *not-positively-green ⇒ defer*. Phase 3 `P3-D9` restates the split: launcher = `/dev/tcp` + bash JSON; `lib.sh` = `curl` (unconstrained). |
| **Pins** | Phase 2 `P-11` → consolidated `P3-PIN-12`: a static scan asserting **zero** executable `curl`/`wget`/`nc`/`lsof`/`python3` tokens in `launcher.sh` (comment mentions excluded). Mutation: *"Introduce `curl` into `launcher.sh` ⇒ RED."* |
| **Verdict** | ✅ **COMPLIANT** — no new external dependency surface; the probe uses bash builtins only; the purity property is statically pinned. |

### 4.7 No-live-contact

| Element | Detail |
|---|---|
| **Frozen rule** | No live-install (`/home/nea/agents-ensemble`) contact during planning and implementation; all fixtures synthetic; the daemon-side journal fixture is **generated, never copied from the live install** (its own docstring). Standing critical-note conventions: frozen-binary boot legs must pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** (never `env -u`), scrub `POSTGRES_*` **per-variable** (`env -u POSTGRES_*` is a silent NO-OP), and set `ENSEMBLE_SELF_ENV=sandbox` on a frozen binary's first boot; staged boot smokes are **not isolated** (the binary's path derivation resolves the LIVE install root — `daemon/api.py:2545`, `daemon/tools/upgrade_tools.py:658`). |
| **How honoured** | Phase 1 `A-P1-4` (*"no code was executed, no `promote.sh`/`lib.sh`/`launcher.sh` was run, and no live install path was touched"*); Phase 2 §S-10 (*"zero writes outside the fixture dirs; no `releases/`, no `/home/nea/agents-ensemble` mutation"*); Phase 3's artifact status (*"No `launcher.sh` / `promote.sh` / `lib.sh` executed"*) and `P3-R7` / `P3-PIN-15` (the three env pins, the F1 fake-HOME fence at `tests/upgrade_test_lib.sh:69-102`, and the "staged smokes are not isolated" rule). |
| **This register** | Planning-only: all verification was read-only `sed`/`grep`/`git status` inside the worktree; **no** `launcher.sh` / `promote.sh` / `lib.sh` was executed and **no** live path was touched. `git status --porcelain` in the worktree shows only the untracked planning directory. |
| **Verdict** | ✅ **COMPLIANT.** |

### 4.8 Cross-phase constraint summary

| Constraint | Owner phase | Honoured by | Verdict |
|---|---|---|---|
| The three phase files are **not modified** by this commission | all | This register writes a **fourth** file; `git status` shows only the untracked planning dir | ✅ |
| IDs are **preserved, never renumbered** | P3 | §2 + §3 cross-ref columns; §5 reproduces Phase 3 §4.4.1 | ✅ |
| The owner ruling is **verbatim** where quoted | all | §1.3 (verbatim), §3 assessments (quoted, not paraphrased as authority) | ✅ |
| Phase 2 must land before (or with) the rider | P2/P3 | §7 sequencing; `P3-D8` | ✅ |
| The tier assignment is **lane-level** | P1/P3 | §2.5 Tension #1 (settled) | ✅ |

---

## 5. TEST-SURFACE MAP (summary)

**Full detail lives in `phase3-plan.md` §4.2.2.** This section is the one-screen index. IDs are Phase 3's consolidated ids (preserved per `OQ-C-25`), with the owning phase's own id in the cross-ref column.

### 5.1 The consolidated pin table

| Consolidated ID | Owning phase / local id | One-line claim | Tier |
|---|---|---|---|
| **P3-PIN-01** | P3 rider | On this GNU host `_js_count_rollback` arms `cooldown_until` **born ~600 s in the FUTURE**, not NOW | T1 |
| **P3-PIN-02** | P3 rider | Both `uname` branches **select** correctly (Darwin → BSD arm, Linux → GNU arm, unrecognized → the policy arm) | T1 |
| **P3-PIN-03** | P3 rider | **Zero** bare `date -jur` sites outside the BSD arm; the GNU arm present | T0 (static) |
| **P3-PIN-04** | P3 rider | **Cross-component round-trip**: the launcher-written value is consumed by `lib.sh`'s `journal_cooldown_active` as **ACTIVE** | T1 |
| **P3-PIN-05** | P2 `P-1` | **Direction ①**: stale flipped txn + **SERVING-GREEN** ⇒ commit-close (`serving_green_commit`; no `sweep_rollback`, no quarantine, counter unchanged, cooldown unarmed) | T1 |
| **P3-PIN-06** | P2 `P-2` | **Direction ②**: stale flipped txn + **SERVING-UNHEALTHY** ⇒ the existing never-green rollback **still fires** (row + quarantine + counter++ + cooldown + `current`→prev) **[PLANNER r4 → BLOCKER 4 — SUPERSEDED for the immediate arm; the row is KEPT, not deleted]** under the r2/r3 observation taxonomy (AM-P2-10.1) only the **BROKEN** class (`/livez` non-200 / unparseable) fires **immediately**; `hard-red` (livez 200 + `/readyz` 500) is **AMBIGUOUS (unready)** and a no-listener observation is **AMBIGUOUS (absent)** ⇒ both **defer** and are **counted** toward the bounded absent-ceiling, and the chain fires when the ceiling predicate holds ⇒ **drive `P3-PIN-20` / `P3-PIN-26`** (the ceiling) + the `livez-non200` fixture mode for the BROKEN leg | T1 |
| **P3-PIN-07** | P2 `P-7` | **Tonight's exact 4-stage sequence**: halt → serving-green → commit-close + `quarantine_cleared_serving_green` ⇒ the shell gate **accepts** the next promote | T1 |
| **P3-PIN-08** | P2 `P-3` | **C5 re-shape** → C5a (green ⇒ commit-close) / C5b (not-green ⇒ the existing chain fires). *Re-shaped, not deleted.* **[PLANNER r4 → BLOCKER 4 — SUPERSEDED for the C5b arm; the row is KEPT, not deleted]** C5b is no longer a single *"not-green ⇒ fires"* assertion: it must be **parameterised on the observation class** per **AM-P2-10.1** (**BROKEN** ⇒ the chain fires **immediately**; **AMBIGUOUS** ⇒ **defer** + count toward the bounded ceiling, the chain firing only when the ceiling predicate holds) ⇒ **drive `P3-PIN-20` / `P3-PIN-26`** for the AMBIGUOUS leg and the `livez-non200` fixture mode for the BROKEN leg | T1 |
| **P3-PIN-09** | P1 `L1`/`L2`/`L4` (+`L3` static) | A **slow unit-active→serving boot** inside the dedicated hand-back budget does **not** trip the abort predicate (the file currently has **no** slow-boot timing pin — the gap) | T1 |
| **P3-PIN-10** | P2 `P-4` | The launcher **defer** path is **BOUNDED**: not-green ⇒ a `sweep_deferred` row with `in_flight` still open and **no other field mutated**; after `SWEEP_DEFER_CEILING` deferrals ⇒ the rollback fires **[PLANNER r4 → BLOCKER 4 — SUPERSEDED on two points; the row is KEPT, not deleted]** (i) the **counter's home** is the launcher-local `.sweep-defer-<run_id>` file (**AM-P2-5**) — **not** a journal-history count; (ii) the firing condition is the **ceiling predicate** (≥`SWEEP_DEFER_CEILING` **qualifying** observations ∧ owner-dead ∧ `intent_flip` ∧ **never-green-observed**, **AM-P2-10.2**) — not "after N deferrals" — and a GREEN observation makes the ceiling **permanently ineligible** for that txn (sticky-green, **AM-P2-10.3**) ⇒ **drive `P3-PIN-20` / `P3-PIN-26`** | T1 |
| **P3-PIN-11** | P2 `P-6` | The **daemon-side** tick: flipped+stale+owner-dead ⇒ green ⇒ commit-close (+ clearance row); unhealthy ⇒ the existing rollback still fires; stale/owner-alive ⇒ **no action** **[PLANNER r4 → BLOCKER 4 — SUPERSEDED on the unhealthy arm; the row is KEPT, not deleted]** the unhealthy arm is **split** by the r3 fold: a **degraded-but-grace-held** composite ⇒ **DEFER** (never a verdict or a request) ⇒ **drive `P3-PIN-27`** for the defer leg; the never-green leg (`P3-PIN-11a`) must use a **not-grace-held** fixture so the two legs cannot be satisfied by the same stub | T1 |
| **P3-PIN-12** | P2 `P-8`,`P-9`,`P-11`,`P-12` | The never-green protection is **UNTOUCHED** (consumer-side); the adopt path mirrors the polarity; the launcher contains **no** executable `curl`/`wget`/`nc`/`lsof`/`python3`; the closer re-reads the txn under the journal lock. **[PLANNER r5 → BLOCKER 5 — THE ADOPT-PARITY CLAIM IS SCOPED, NOT UNSCOPED (append-only; the text above is retained for the audit trail).]** *"the adopt path mirrors the polarity"* **cannot hold literally for every fixture**: on the **GREEN-without-`intent_flip`** fixture the three decision-makers **deliberately diverge** — the launcher **defers** (§D.2 rung 1), the daemon takes **no action this tick** (§D.1 P9), the adopt path takes the **existing chain** (`phase2-plan.md` §D.4 (a-i); the adopt path's existing chain is its pre-ruling behaviour and this lane does not rewrite it). ⇒ the shared-polarity pin is **scoped to the fixtures where all three agree** — **GREEN-and-authorized** and **NOT-GREEN** — and the GREEN-but-unauthorized fixture is pinned **per decision-maker** (`P-26` / `P-15`'s negative axis for the launcher + daemon legs, plus the adopt-path leg of the same pin). The **same scoping** is carried by `phase2-plan.md`'s **R-8** row (folded in the same pass), by **§D.4 (a-i)**, and by `architecture-recommendation.md`'s reconciled-dispositions table (the *GREEN / no-`intent_flip`* row). **The divergence is disclosed, not a defect to be unified.** The other three assertions in this cell (never-green untouched; launcher purity; lock + re-read) are **UNCHANGED**. | T1 |
| **P3-PIN-13** | P3 deployment | The **two-sided requirement**: the stamp must be 40-hex, resolvable, and **EQUAL** the floor; a mismatch refuses (exit-78 class) | T1 |
| **P3-PIN-14** | P3 deployment | `stage.sh` **refuses to stage** when the floor is unset / not-a-sha / not-an-ancestor of the built tree | T1 |
| **P3-PIN-15** | all (harness) | Every frozen-binary boot leg pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly**, scrubs `POSTGRES_*` **per-variable**, sets `ENSEMBLE_SELF_ENV=sandbox` | T0 (self-check) |
| **P3-PIN-16** | all (docs) | Every pack registered in `.agents/tester/PACKS.md` with **command + scope + verdict** (keep-both-newest-first) | T0 (doc) |
| **P3-PIN-17** | all (lane-level) | **Tier-2 whole-tree dual-side leg**: net-new reds = 0 at the lane tip, base↔HEAD | T2 (≤90 min advisory) |
| **P3-PIN-18** **[PLANNER r5]** | P2 `P-10`, AM-P2-4, AM-P3-2 | **Version normalization per comparison site** — one mutation-verified leg per site (launcher probe / daemon closer / adopt path): a `v`-prefixed target matches a bare `__version__` ⇒ GREEN on **every** site; an anti-`tr -d v` leg (the strip is `${x#v}` — one leading `v`, idempotent). *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-19** **[PLANNER r5]** | P2 `P-21`, AM-P2-8 | **The promote-wake reader is `run_id`-scoped** ⇒ an unrelated commit-close cannot silently **drop** a pending promote-wake (a paused instance never resumes). **Reframed (r2 → #22) as a PRE-EXISTING-DEFECT CURE**, not a new-event problem — the reader's `run_id`-blindness is pre-existing and today's `boot_sweep_commit_and_continue` already exposes it; the pin stays because the cure is the point. *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-20** **[PLANNER r5]** | P2 `P-20`, AM-P2-5, AM-P2-10 | **The defer counter's HOME + the absent-ceiling eligibility** — the tally lives in the launcher-local file `<INSTALL_DIR>/.sweep-defer-<run_id>` (O(1), survives launcher restarts; **not** the journal history, whose extraction measured 13.5 s / 35.7 s at 27 KB) and the **bounded absent-ceiling** fires the existing chain only when ≥`SWEEP_DEFER_CEILING` **qualifying** observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed hold. *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-21** **[PLANNER r5]** | P2 `AM-P2-4` | **THE THREE-AXIS `intent_flip` PIN** (the number the council mandated): (a) **GREEN ∧ `intent_flip` ⇒ commit-close** (no quarantine / counter / cooldown) · (b) **GREEN ∧ NO `intent_flip` ⇒ defer / no-action / existing-chain — NEVER commit** · (c) **serving-unhealthy ⇒ the existing never-green rollback still fires**; plus the r2 negative axis (**forged** `rollback_requested` + green + no `intent_flip` ⇒ defer). **[PLANNER r5 → BLOCKER 1]** this row is the consolidated-map carrier of the **daemon-side** authorization conjunct that the r5 fold promoted into `phase2-plan.md` §D.1's predicate table (**P9**). *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-22** **[PLANNER r5]** | P2 `P-13`..`P-18`, AM-P2-6 | **The T14 handoff family** — request + NOT-GREEN ⇒ the existing chain executes with a post-state **byte-identical** to the legacy no-request path; no request ⇒ the defer ladder unchanged; request + GREEN ⇒ commit-close, the request **not** executed; the ceiling path and the request path converge on **one** chain ⇒ identical post-state; a request naming another `run_id`/target is ignored. *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-23** **[PLANNER r5]** | P2 `P-19`, AM-P2-1 | **Crash-prefix safety** — interrupt at **each** prefix of the commit-close sequence ⇒ the next actor's disposition is **never** a wrongful rollback and the sequence converges (re-run is a no-op or completes it). Reordering the sequence (close before history) ⇒ **RED** (a serving-green release ends up permanently quarantined). *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-24** **[PLANNER r5]** | P2 `P-22`, AM-P2-8 | **The vocabulary-growth test updates are applied** (the gate reddens **predictably**, not as a surprise): `len(uj._TERMINAL_EVENTS) == 9` ⇒ 10 + set-equality (`tests/unit/services/test_post_restart_arm_notify_sweep.py:875`) and the label-coverage update (`_OUTCOME_LABELS["serving_green_commit"]`, `tests/unit/tools/test_upgrade_tools.py:886-899`). *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-25** **[PLANNER r5]** | P2 `AM-P2-9` | **The probe acceptance mutations** — a truncated/unparseable body ⇒ **NOT-GREEN**; the wall-clock deadline **inside** the body-read loop (a per-read `-t` alone is defeatable by a slow-dribble responder); the `/readyz` second GET over the same `/dev/tcp` fd is present and answered; a wrong version **fails fast**. *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-26** **[PLANNER r5]** | P2 `P-23`,`P-24`,`P-27`,`P-29`,`P-30`,`P-32`,`P-33`, AM-P2-10 | **THE r2 FAMILY, in one place** — (a) the absent-ceiling **fires** after `SWEEP_DEFER_CEILING` qualifying observations driven across **separate `run_sweep` invocations** (the counter persists) ⇒ the existing chain with all **four** effects; below the threshold ⇒ defer · (b) **never-green-observed is sticky** — a GREEN at any point (or a `serving_green_commit` row) makes the ceiling permanently ineligible for that txn · (c) the symlink-state split (AM-P2-16) · (d) the narrow T15 helper (AM-P2-17) · (e) the kill-switch `SWEEP_SERVING_GREEN_ENABLED`. *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-27** **[PLANNER r5]** | P2 `P-35`, AM-P2-24 | **THE DEGRADED-BUT-GRACE-HELD ⇒ DEFER LEG** (F-1) — a stubbed `readiness_provider` returns a composite with `ready == False` ∧ `grace_active == True` against a stale flipped txn ⇒ the daemon-side closer writes **no** `serving_unhealthy` and **no** `rollback_requested`, the txn stays **open**, `current` is **not** repointed. *Full row: `phase3-plan.md` §4.2.2.* | T1 |
| **P3-PIN-28** **[PLANNER r4]** | P2 `P-36` | **The quarantine-clearance reader's shape** — the two negatives (prefix collision `target v0.28.55` must not clear `v0.28.5`; cross-entry split must not clear) + the positive (the pinned house detail `target v0.28.5 run_id <ID> …` clears) — a reader that never fires is a regression to tonight's punishment class | T1 |
| **P3-PIN-29** **[PLANNER r4]** | P2 `P-37` | **The REAL GraceTracker / composite-interleaving leg** — the closer driven against a real `ReadinessComposite` whose `grace_active` is produced by the real `GraceTracker` (grace-held ⇒ no verdict; after a full lapse ⇒ the never-green verdict fires) — `P-35`/`P3-PIN-27` proves the closer's conjunct against a synthetic composite | T1 |
| **P3-PIN-30** **[PLANNER r4]** | P2 `P-38` | **The daemon-side kill-switch, two states** — `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER=0` ⇒ the lane writes no `serving_green_commit` / `serving_unhealthy` / `rollback_requested` and leaves the txn untouched; `=1` (default) ⇒ normal behaviour; read per tick, not cached | T1 |

**Phase-1 legs folded into the map** (phase1-plan.md §9; legs L1-L12, mutations M1-M10): `L1` budget-source discrimination, `L2` default-budget coverage, `L3` default value (static), `L4` budget expiry (fail direction), `L5` equal-to-prestop fast-fail preserved (exactly one poll), `L6a` port-up-but-not-serving ⇒ NOT success, `L6b` serving-but-wrong-version ⇒ fail fast, `L6c` serving-with-the-right-version ⇒ success, `L6d` `lsof`-absent + `curl`-present ⇒ degraded serving-only, `L6e` `PORT` unset ⇒ MainPID-alone, `L7` squeeze ⇒ the **floor** is honoured, `L8` roomy ⇒ the **ceiling** is honoured, `L9` grace fires on progress, `L10` grace does **not** fire without progress, `L11` knob validation (garbage ⇒ default + loud stderr), `L12` 7b substring trap on the new lines. Mutations M1-M10 make each leg discriminating (e.g. M2 *"Drop the serving conjunct (lsof alone confirms again)"* ⇒ L6a RED; M4 *"Remove the floor clamp"* ⇒ L7 RED).

**Phase-2 pins folded into the map** (`P-1`…`P-12` → §5.1; `P-5` probe-primitive legs and `P-10` the version-string asymmetry are covered inside `P3-PIN-05/-06/-11` and owned by Phase 2's implementation).

### 5.2 The lane Tier-2 mandate + the recipes

- **Tier 2 is MANDATORY for the lane** (`P3-D6`), for two **independent** mechanical triggers: (1) the diff touches **`daemon/api.py`** — a listed core seam (`ensure.md:19`) — and also `daemon/manager.py`; (2) the gate is **promote-blocking by construction** (*"this lane IS the promote pipeline"*). The rider's own pins remain **Tier 1**. §2.5 Tension #1 records the reconciliation with Phase 1's own Tier-1 statement.
- **Mandatory recipe load** before any leg (the recipe dirs **are** the gate recipes; `ensure.md` §"All tiers — non-negotiable"): `blast-radius-leg` (T1), `whole-tree-base-proven-diff` (T2 — the phase notes the brief's guess `whole-tree-base-proded-diff` was wrong and the *verified* name is `whole-tree-base-proven-diff`), `regression-ab-partition-run`, `ab-base-leg-provisioning-artifacts`, `failed-digest-equality-gate`.
- **Additional Tier-2 hygiene the map requires:** a **node-cap + measured-rate projection guard** before the whole-tree leg, and a **pre-exclusion registry** (what is excluded and why, recorded **before** the run).
- **Proof form** (`ensure.md:27`): mechanical derivation → declared set → `--collect-only` slicing proof (executed == declared, counts recorded) → identical commands both legs → *red-at-base ≠ net-new* → 3× flaky isolation on diff nodes only → FAILED-digest comparison per `failed-digest-equality-gate`.
- **Budgets:** Tier 1 ≤ 30 min **HARD** (abort + report + ONE auto re-scope attempt; never silent grinding); Tier 2 ≤ 90 min advisory, fail-loud reporting still applies. The 5-min cap is **per test pack**, not per gate leg.

### 5.3 The pre-existing base-red seal tripwire — handling

`tests/test_chart_image_delivery_e2e.py:1425` holds `SEALED_SHA_BASELINES`; `daemon/constants.py` is in that set (`:1467`) with baseline `464a2459fb4c91c6a1add2bc6bafc3b1242415996597f4e16bef44b3387c8e92`, while the worktree's content hashes to `5dd9bfa3e56db333d4cac92b78bba133ef26a327c6810312d819cd6e437a47b7` — **the two already differ at base**. The tripwire `test_phase_d_does_not_modify_sealed_artifacts` (`:1515-1535`) therefore fails **at base, before any lane edit**.

**Handling (per `P3-R10` / `OQ-C-24`):**
1. **Record it as a base red on BOTH legs** in the FAILED-digest comparison (the `red-at-base ≠ net-new` rule, `ensure.md:27`).
2. **Do NOT re-pin the seal in this lane** — that would fold pre-existing base debt into the lane's diff and mask the fact that the seal has been stale since before this commission; route the re-pin to its own small lane.
3. **Name it explicitly** in the tester's report (file, node id, both hashes) so it is auditable rather than merely inferable from the digest diff (this register's addition to the phase's treatment).
4. Phase 2 touches `daemon/constants.py`, so the lane **will** encounter this red — the expectation must be pre-declared, or an implementer may "fix" it.

---

## 6. DEPLOYMENT MAPPING (summary)

### 6.1 The two-sided split

| Surface | Ships in the release payload? | How it reaches live | Anchor |
|---|---|---|---|
| **`launcher.sh`** | **YES** | **Promote only.** Swapped into the install in the promote's stopped window; executes at the **next launcher boot**. | `scripts/upgrade/stage.sh:424` (copy), `:11-14` (payload header), `:438` (`LAUNCHER_SHA`), `:599` (manifest `launcher_sha256`) |
| **`daemon/services/upgrade_journal_sweep.py`, `daemon/api.py`, `daemon/tools/upgrade_journal.py`, `daemon/tools/upgrade_tools.py`** | **YES** — inside the frozen binary (`ensemble-prod`) | **Promote only** (the binary is replaced wholesale). | `stage.sh:395-433` (payload composition: binary `:395`, `agents/` `:397`, `frontend/` `:398-400`, `plugins/` `:404`, `config.yaml` `:423`, `launcher.sh` `:424`) |
| **`scripts/upgrade/{lib.sh,promote.sh,stage.sh}`** | **NO — source-less.** The payload is binary + `agents/` + `frontend/` + `plugins/` + `config.yaml` + `launcher.sh` **only** | **Ceremony-time execution** from the `ENSEMBLE_UPGRADE_SCRIPTS_DIR`-pinned directory. Consumed **daemon-side only** (`_resolve_scripts_dir`: explicit env → repo-of-module → `<install_dir>/scripts/upgrade`); armed calls refuse `executor-scripts-unavailable` (fail-closed). **Zero** references to `scripts/upgrade` from any shell script. | `daemon/tools/upgrade_tools.py:1979-2002`, call sites `:2887`, `:3572` |

**Consequence for this lane (Phase 3 `P3-R1`):** the rider's fix lives in `launcher.sh` ⇒ **source-side only** ⇒ it reaches live **at the next promote**, not at the next commit. Between merge and promote, the launcher's cooldown arm is still the defective one. The interim protections are Phase 2's daemon-side closer (which acts at the first sweep tick after the 600 s staleness gate — `launcher.sh:69`, `:906` — i.e. ~11 min, long before the ~59 min fuse) and the launcher's defer path. **[PLANNER r4 → note (g) THE "~59 min FUSE" IS NOT A DESIGNED FUSE]** the 3540 s figure was the **age of tonight's *unconditional* auto-rollback** (a launcher boot at 22:25:53 on a txn opened ≈21:26) — there is **no** 3540 s constant anywhere in the tree (verified: `grep -rn 3540 scripts/upgrade/ launcher.sh` ⇒ 0 hits); the launcher's gate is `SWEEP_STALE_S=600` (`launcher.sh:69`) plus the age computed at launcher entry (`:907`). ⇒ the **operative bound under this plan is the ≈10–15 min bounded absent-ceiling** (3 qualifying launcher observations × ≈3.5 min per boot in the `RestartSec=10` + ~200 s dying-boot environment). **Launcher/daemon precedence:** the launcher sweeps **pre-daemon** (`launcher.sh:1661-1662` vs the daemon child at `:1525`) ⇒ within one launcher boot the launcher observes first; the daemon closer ticks every 90 s while the daemon is up and reaches its verdict at the **first tick after the 600 s staleness gate** (≈11 min) ⇒ **the daemon closer normally wins the race** and the launcher's bounded ceiling is the backstop for the shapes the daemon cannot reach (never-started daemon; tempfail-75 / clean-exit-0). **This is exactly why `OQ-C-22` (same release) is answered "yes"** — a later release would re-open the interim window after the closer is already live.

### 6.2 THE TWO-SIDED REQUIREMENT (the release-bump obligation)

> **The release bump commit MUST set BOTH**
> - `daemon/tools/upgrade_tools.py:3916` → `PIPELINE_SCRIPTS_FLOOR_COMMIT` (currently `953603108fa35d4f59031818c3091eecda8c1663`), **and**
> - `scripts/upgrade/lib.sh:95` → `PIPELINE_SCRIPTS_REV` (currently the same sha),
>
> **to the SAME merge commit** — the commit that introduces this lane's ceremony-relevant `scripts/upgrade` + promote semantics. The two *travel together* by house doctrine (D6-8 / T6.15 / AC15); the daemon-side constant and the scripts-side stamp are two provenance channels for one fact.

Both are verified to currently carry the same sha (`upgrade_tools.py:3916` and `lib.sh:95`).

**Enforcement — all mechanical, all already shipped:**

| Gate | Site | Behaviour on violation |
|---|---|---|
| Floor is set and is a sha | `stage.sh:556` (extract), `:557-560` (refusal) | `exit 78` — **REFUSING TO STAGE** |
| Floor resolves and is an **ancestor of the built tree** | `stage.sh:562-565` (`git merge-base --is-ancestor`) | `exit 78` — **REFUSING TO STAGE** |
| Stamp is 40-hex | `lib.sh:596-600` (`_pipeline_scripts_stamp_verdict`) | `exit 78` — **REFUSING TO STAGE** (via `stage.sh:582-586`) |
| Stamp resolves in the tree | `lib.sh:601-603` | `exit 78` — **REFUSING TO STAGE** |
| Stamp **== floor** | `lib.sh:604-606` (*"the two travel together"*) | `exit 78` — **REFUSING TO STAGE** |
| Manifest carries the floor | `stage.sh:595` (`pipeline_scripts_floor_commit`) | The staged artifact is self-describing about the floor it will enforce |

**This lane's obligation (task `P3-T08`):** the giter lane sets both constants in the **release bump commit** — *"the house 4-file bump set plus these two value-carrying lines; `upgrade_tools.py` and `lib.sh` are **not** version files, so the bump is a targeted edit, never `git add -A`"* — then re-stages and confirms the floor+stamp validation passes and the manifest's `pipeline_scripts_floor_commit` equals the floor. **Executable proof: `P3-PIN-13` + `P3-PIN-14`.**

### 6.3 The USER-OWNED ceremony precondition

> The live `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin must be advanced to a tree whose HEAD has the **new floor commit as an ancestor**. This is an **operator/user action**, surfaced as a ceremony precondition in the lane's operator-facing doc — **not** this lane's work.

**Enforcement point:** the **arm-time provenance guard** — `daemon/tools/upgrade_tools.py:3701-3731` (Check 4, phase 6) — is judged **before** `consume_pending_action`, so a refusal **never burns the nonce** (`:3707-3712`). An **explicit** `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin is judged **REFUSE**; a **fallback** resolution (module-repo / `install_dir/scripts`) is downgraded to **WARNING** only (`:3723-3733`).

**Consequence table (Phase 3 §4.3.4):**

| Condition | Consequence |
|---|---|
| Floor + stamp **not** both set to the same merge commit | `stage.sh` **refuses to stage** (`exit 78`) — loud, early, at stage time, not ceremony time |
| Live `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` **not** advanced to a tree containing the new floor as an ancestor | The next scripted ceremony **refuses pre-nonce, BY DESIGN**; the nonce is **not** burned — *the guard working, not a regression* |
| A re-stage is attempted from a non-tip tree | Refused by the **L1 tip-identity guard** (`exit 78`); promote the staged artifact **AS-IS**; do not override |
| The rider's `launcher.sh` fix merged but not yet promoted | The **live** launcher still has the BSD-only cooldown arm until the next promote; interim protection = Phase 2's daemon-side closer + the launcher's defer path (`P3-R1`) |
| **[PLANNER r4 → note (l)] The ceremony's disposition doctrine is stated at the gate** | The ceremony notes must carry the reconciled sentence — **serving-green ⇒ NEVER rolled back** (the owner ruling, absolute) vs **serving-unhealthy ⇒ the existing chain still fires, BOUNDED** by the absent-ceiling (≥3 qualifying AMBIGUOUS observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed). The commission's literal *"NEVER rollback"* wording is **not** the ratified rule and must not be read as one; the bounded ceiling is a **leader-ratified bounded ceiling** (OQ-C-01/OQ-C-06/AM-P2-10) and is surfaced here so the gate and the register cannot be read as contradicting each other |

---

## 7. SEQUENCING

### 7.1 The binding order

```
┌─ Phase 2 (serving-green split closer) ──────────────────────────────┐
│  launcher.sh disposition branch + daemon closer + adopt path +      │
│  quarantine clearance (both sides) + event vocabulary               │
└─────────────────────────────────────────────────────────────────────┘
                 │
                 ├─► Phase 3 rider (launcher cooldown)  — AFTER Phase 2,
                 │     or folded IN-COMMIT with it (P3-D8)
                 │
                 └─► Phase 1 (hand-back verify budget) — INDEPENDENT
                       (must not assume Phase 2 landed; and Phase 2 must
                        not assume Phase 1 landed — phase2-plan.md §Coupling)
```

**Phase 2 → rider (binding).** Phase 2 rewrites the **disposition decision** inside `launcher.sh`'s flipped branch; the rider rewrites `_js_count_rollback` (`launcher.sh:459-478`), which is *called by* the chain Phase 2 re-orders. **Phase 2 must land first** (or the rider must be folded in-commit with it); the rider must not be implemented on top of the pre-Phase-2 chain. **Why it matters concretely:** the rider's *"cooldown armed ONLY on the rollback path"* assertion (`OQ-C-23`) is **vacuous** against the pre-Phase-2 sweep, because that sweep arms the cooldown on *every* stale-flipped txn. The two edits touch **different functions** (`_journal_sweep`'s disposition branch vs `_js_count_rollback`), so an in-commit fold is mechanical and conflict-free.

**Phase 1 independence (binding, both directions).** Phase 1 changes *how long* the hand-back verifier waits; Phase 2 changes *what happens to the txn it leaves behind*. Phase 1 reduces the probability of a halt; Phase 2 makes a halt non-catastrophic. **Phase 2 must not assume Phase 1 landed** (the halt path must remain correct with the 180 s budget unchanged), and Phase 1 assumes Phase 2 will make a serving-green release immune to wrongful rollback (`A-P1-3`) — *"If Phase 2 lands differently, the grace extension (OQ-P1-4 / `OQ-C-04`) becomes more important, not less."*

### 7.2 The same-release requirement

**The rider and the Phase-2 closer must ship in the SAME release** (`OQ-C-22`, `P3-Q7`). Rationale in §3.3 `OQ-C-22`: the rider's fix is source-side only and reaches live at the next promote; if it ships in a *later* release than the closer, the interim window (during which the live launcher arms a born-expired cooldown) is not shortened but **re-opened** after the closer is already live. The code-ordering constraint (Phase 2 first, or in-commit) and the packaging constraint (same release) are **compatible** because the two edits touch different functions.

### 7.3 Stage-time order (the giter lane)

1. Phase 2 lands (or the rider folds in-commit) → 2. Phase 3 rider pins → 3. Phase 1 pins → 4. Tier-1 scoped leg (≤ 30 min HARD, fail-loud) → 5. **Tier-2 whole-tree dual-side leg** (≤ 90 min advisory) — `P3-PIN-17`, **mandatory** → 6. **the release bump commit sets BOTH** `PIPELINE_SCRIPTS_FLOOR_COMMIT` (`upgrade_tools.py:3916`) **and** `PIPELINE_SCRIPTS_REV` (`lib.sh:95`) to the **same merge commit** (targeted `git add --`, never `-A`) → 7. stage: the floor/stamp validation must pass (`stage.sh:556-565`, `:582-586`; `lib.sh:594-615`) and the manifest must carry `pipeline_scripts_floor_commit` (`:595`) → 8. the **user** advances the live `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin (ceremony precondition, §6.3) → 9. the ceremony's arm-time guard (`upgrade_tools.py:3701-3731`) refuses pre-nonce until that pin is advanced — **by design**, nonce not burned.

### 7.4 What this register does **not** decide

- The **owner ruling** is settled and is not reopened here (§1.3).
- The **never-green path** stays armed; the serving-green path is new; the composition requirement is proven in both directions (§4.5).
- The **phase-arithmetic policy** (`OQ-C-01`) is a **LEADER** call with an explicit downstream work-list if option (a) is chosen.
- The **rollback-execution mechanism** (`OQ-C-07`) is an **ARCHITECT** pressure-test, with three amendments recorded in §3.2.
- The **unrecognized-platform arm** (`OQ-C-17`) is an **ARCHITECT** call, with the phase's option (ii) shown not to deliver its stated outcome.
- The **seal re-pin** (`OQ-C-24`) is a **LEADER** call, with the strong recommendation to keep the base-red visible.

---

## Appendix A — Consolidated id cross-reference

**[PLANNER r5 → SAME-PASS NOTE (the id-inventory was stale) — the ranges below are RE-DERIVED from the live files at this fold (append-only; the stale ranges are retained in the audit trail).]** The r4 fold added ids that the inventory never absorbed, so the table understated the plan set's real id surface. **Nothing was renumbered or deleted — the ranges are widened to the ids that actually exist.** Re-derived counts (the `grep`-based counts are reproducible by the same command):

| Consolidated | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| `OQ-C-01`..`OQ-C-05` | `OQ-P1-1`..`OQ-P1-5` | — | — |
| `OQ-C-06`..`OQ-C-15` | — | `OQ-1`..`OQ-10` | — |
| `OQ-C-16`..`OQ-C-25` | — | — | `P3-Q1`..`P3-Q10` |
| Decisions | `D-P1-1`..`D-P1-10` | `D.0`..`D.7`, `A1`..`A6`, `T5` (omission), **`AM-P2-1`..`AM-P2-24`** (amendment series) | `P3-D1`..`P3-D9` |
| Tasks | `T-P1-1`..`T-P1-12` | `T1`..`T18` (**T14** = the daemon→launcher handoff, **T15** = the run-loop re-sweep hook, **T17**/`T18` = the probe / grace rows) | `P3-T01`..`P3-T13` |
| Pins | legs `L1`..`L5`, **`L6a`..`L6e`**, `L7`..`L12`, **`L13`..`L15`** [DEV-FOLD-2026-11-02 — approver iter003 note 1: the r5 re-derivation stopped at L12; L13-L15 live at `phase1-plan.md:536-538`], mutations `M1`..`M13`, `AC-P1-1`..`AC-P1-19` | `P-1`..`P-38`, `S-1`..`S-14` | `P3-PIN-01`..`P3-PIN-30`, `AC-P3-1`..`AC-P3-12`, `P3-T01`..`P3-T13`, `P3-R1`..`P3-R10` |
| Risks | `R-P1-1`..`R-P1-15` [DEV-FOLD-2026-11-02 — approver iter003 note 1: the risk family was absent from the inventory; documented in the register fold sections `:1035`/`:1084`] | `R-1`..`R-18` | `P3-R1`..`P3-R10` |
| Cross-referenced into the consolidated pin table | `L1`/`L2`/`L4` → `P3-PIN-09` | `P-1`→`05`, `P-2`→`06`, `P-7`→`07`, `P-3`→`08`, `P-4`→`10`, `P-6`→`11`, `P-8`/`P-9`/`P-11`/`P-12`→`12`; `P-5`/`P-10` folded into `05`/`06`/`11`; **`P-13`..`P-18`→`P3-PIN-22`**; **`P-19`→`P3-PIN-23`**; **`P-20`→`P3-PIN-20`**; **`P-21`→`P3-PIN-19`**; **`P-22`→`P3-PIN-24`**; **`P-23`..`P-30`,`P-32`,`P-33`→`P3-PIN-26`**; **`P-35`→`P3-PIN-27`**; **`P-36`→`P3-PIN-28`**; **`P-37`→`P3-PIN-29`**; **`P-38`→`P3-PIN-30`**; **`AM-P2-4`→`P3-PIN-21`** | `P3-PIN-01`..`30` (native; **all 30 rows are now present in §5.1** — the ten the r2/r3 folds added (`18`..`27`) were folded into the register's consolidated table by the [PLANNER r5] pass) |

**Preserved verbatim, not renumbered** (per `OQ-C-25` / `P3-Q10`).

## Appendix B — Corrections recorded by this register

| # | Item | Where | Disposition |
|---|---|---|---|
| B-1 | Incident run id typo `r-20261021-212542-8279` → correct **`r-20261101-212542-8279`** | `phase3-plan.md:3`, `:22` | Recorded here; **phase files not modified** (commission constraint). Rides with the doc-only rider (`OQ-C-20`). |
| B-2 | KV `research_C_daemon` cites the canonical cooldown twin at `lib.sh:1375-1385`; that range holds `JOURNAL_SIZE_GUARD_BYTES` / `JOURNAL_LEGACY_READ_BUDGET_S` | KV `design_guidance_leader` (SCOPE RIDER) and `research_C_daemon` | The real dispatch is **`lib.sh:2099-2107`** (verified). Phase 3 already records this in §4.1.5 / §12; this register confirms it. |
| B-3 | Phase 2 anchors `EXECUTOR_STALENESS_WINDOW_S` at `upgrade_journal_sweep.py:110`; the constant is declared at **`:112`** | phase2-plan.md §D.1 P6 | One-line drift, no substance. |
| B-4 | The leader's SCOPE RIDER says the fix should mirror `lib.sh:1375-1385` | KV `design_guidance_leader` | Same as B-2 — the mirror target is `lib.sh:2099-2107`. |


---

## 8. [ARCHITECT r1] DISPOSITIONS — architect enrichment pass, 2026-11-02

Review wave: 3 workers (`resilience-design` 47ed7327 / `data-flow-design` 3d9c4a94 / `structural-design` 2d8f681f), all load-bearing anchors re-verified in-tree at `941cb6321`; the live journal was read read-only for ground truth (27,302B / 157 entries; `current=v0.28.4`, `quarantined=['v0.28.5']`, `rollback_window_count` 24h = **2/3**, `cooldown_until` in the past — zero-length, corroborating the rider defect live). Amendments are folded as `[ARCHITECT r1]` sections in each phase file; synthesis in `architecture-recommendation.md` (this directory).

**Verdict: SOUND-WITH-AMENDMENTS** — no phase contradicts a binding ruling; three 🔴 gaps closed by amendment; one register premise corrected; two numbered tasks (T14, T15) and nine pins (P-13..P-22, P3-PIN-18/19/20) added.

### 8.1 Shortlist dispositions (the §3.0 gate items)

| ID | Disposition | One-line |
|---|---|---|
| **OQ-C-01** | **RULED (leader) + amended** | 900 stays; window-share confirmed; the grace is made deadline-aware (AM-P1-3) so the fix's own extension cannot manufacture the `:598` breach; the register's honest framing — "(b) fixes tonight's case; only (a) fixes the arithmetic" — preserved as the standing disclosure |
| **OQ-C-06** | **ADJUDICATED — premise CORRECTED** | NO 6h promote-txn `in_flight` reaper exists in-tree (the blueprint's reaper is the pending_op/arm lane; `upgrade_journal.py:2034-2036`/`:2262-2272` never touch a flipped promote txn). tempfail-75 / clean-exit-0 are genuinely unbounded AND daemon-unreachable ⇒ T15 run-loop re-sweep hook + operator runbook terminal backstop + residual disclosure (the leader direction executed: extend + disclose, do not block) |
| **OQ-C-07** | **ADJUDICATED — Option 2 CONFIRMED, seam now T14** | Event-based handoff contract (`rollback_requested`, quote-anchored target+run_id conjunction reader), POSITION-0 placement before the probe/defer paths, re-probe precedence (GREEN ⇒ commit-close per the ruling), one-chain execution, pins P-13..P-18. Option 1 rejection recorded with the sharper reason: a second rollback executor would sit on the journal-FIRST side of the documented flip-first preference (`launcher.sh:1131-1136`) — do not revisit as an "efficiency" question |
| **OQ-C-11** | **RULED YES — folded** | P9 predicate added to D.1 + T11 conjunct + NEW `journal_history_has_intent_flip` reader in `lib.sh` (lib.sh has ZERO intent_flip readers today) + canonical predicate text + three-axis mutation-proven pin (AM-P2-4) |
| **OQ-C-17** | **RULED — (iii)-scoped fail-closed marker** | Option (ii) affirmatively refuted by the consumer code (absent field ⇒ ACTIVE fail-closed; stale-expired ⇒ silently disabled); `lib.sh:2105-2107` twin + the recognized-platform `date`-failure fallbacks = named follow-up, out of scope (AM-P3-1) |
| **OQ-C-24** | **RULED (no re-pin) — confirmed** | §5.3 treatment stands: base-red named on both legs, hashes cited, routed to its own lane |
| **OQ-C-22** | **RULED (one release) — confirmed + matrix** | W1–W4 transition matrix added; W1's protection is CONDITIONAL on the scripts-pin advance + same-commit floor bump (AM-P3-2) |

### 8.2 Register corrections recorded by this pass

1. **OQ-C-13 AMENDED** — the history-count mechanism is REPLACED: launcher-local `<INSTALL_DIR>/.sweep-defer-<run_id>` counter + positive-verdict-only ceiling eligibility + single history extraction per boot + launcher-side journal size guard (AM-P2-5). The register's "(a) is effectively forced" was wrong — the constraints also permit a local file, which avoids the measured O(N²) cost (35.7s history extraction at 27KB; ≈285× at the 456KB incident size) and the cross-entry false-positive class.
2. **Alert-map collision hypothesis REFUTED** — `ALERT_KIND_BY_EVENT.get(event)` is an EXACT dict lookup (`upgrade_journal.py:758`): no false `upgrade_auto_rollback` from `quarantine_cleared_serving_green`. The true hazard is SILENCE ⇒ explicit map entries are REQUIRED, not recommended (AM-P2-8).
3. **§6.1's W4 interim sentence is WRONG** — neither named interim protection (daemon closer, launcher defer) is live before the promote; the merge→promote interim has no new protection (AM-P3-2).
4. **OQ-C-11(d)'s "lib.sh has its own history reader" is imprecise** — lib.sh has history WRITERS and `journal_is_quarantined`, but zero intent_flip readers; the reader is new work (AM-P2-4).
5. **OQ-C-14 strengthened** — the history-before-close invariant (AM-P2-1) makes `_TERMINAL_EVENTS` membership crash-safe, not merely semantically right; crash-prefix walk P0–P5 added.

### 8.3 New tasks / pins added (numbering continues the phase series)

- **T14** daemon→launcher rollback-disposition handoff (pins P-13..P-18) — AM-P2-6
- **T15** run-loop re-sweep hook (composes with T14; observation NOT ceiling-eligible) — AM-P2-7
- **P-19** crash-prefix fault-injection (history-before-close) — AM-P2-1
- **P-20** defer-counter home + `≤ CEILING−1` pin — AM-P2-5
- **P-21** wake-reader run_id scoping — AM-P2-8
- **P-22** vocabulary-growth test updates (`_TERMINAL_EVENTS` len pin → 10 + set-equality; `_OUTCOME_LABELS` entry) — AM-P2-8
- **P3-PIN-18/19/20** consolidated: version normalization per-site / wake reader / defer counter — AM-P3-2

### 8.4 Follow-ups named, out of lane

- `lib.sh:2105-2107` unrecognized-platform twin **and** the recognized-platform `date`-failure fallbacks (`:2101-2105`) — ceremony-side cooldown commission.
- `journal_quarantine_clear`'s unanchored `grep -v "^$ver\$"` (`lib.sh:1958`; `.` matches any char — `v0.28.5` vs `v0.28.5x` class) — pre-existing hygiene lane.
- bash 3.2 `read -t`-on-socket verification (OQ-C-12 waiver stands; deterministic NOT-GREEN degradation is the acceptance condition).
- RuntimeMaxSec lockstep cap-raise — only if real ceremonies hit the stop-loss in practice (OQ-C-01 ruling).
- history-array growth (120/157 live entries are `mcp_server_state`) — no trimmer found in-tree; a rotation commission is warranted independent of this lane.

---

## 9. [PLANNER r2] FOLD ROUND 2 — the council REJECT (scoped to Phase 2) + three binding leader rulings — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r2]`; **no existing decision, OQ, pin, task, phase or id is renumbered or deleted**. New ids are appended (`AM-P2-10`..`AM-P2-23`, `AM-P1-5`, `P-23`..`P-34`, `P3-PIN-18`..`P3-PIN-26`, `T14`..`T17`, `T-P1-12`, `L13`..`L15`, `M11`..`M13`, `AC-P1-17`..`AC-P1-19`, `SC-8`, `S-11`..`S-13`, `R-13`..`R-17`, `R-P1-13`/`R-P1-14`).

### 9.1 The council verdict (2/2 converged)

> **REJECT — scoped to Phase 2.** Phases 1 and 3 are approvable with folds.
> **REJECT basis:** `AM-P2-5`'s ceiling-eligibility predicate fired the defer-ceiling **only** on a positive-unhealthy observation or a `rollback_requested` disposition, **never** on an absent/port-down observation. In the canonical never-green shape (the daemon never serves; **every** launcher observation is absent) **nothing rolled back** — a regression against today's unconditional rollback and a violation of the COMPOSITION REQUIREMENT (*"never-green MUST still roll back a broken release"*). **At least four plan texts claimed "never-green stays armed" while the mechanism no longer armed it.**

### 9.2 The three binding leader rulings (folded verbatim in substance)

| Ruling | Disposition | Where it is normative |
|---|---|---|
| **OQ-1 — absent-observation disposition** | **(a) BOUNDED ABSENT-CEILING.** Rollback eligible after **≥3 absent observations AND owner-dead AND intent_flip AND never-green-observed**. Option (b) (any-absent ⇒ rollback) **REFUSED** — it would rewrite an owner-ratified principle; the standing COMPOSITION REQUIREMENT decides. | `phase2-plan.md` **§AM-P2-10** (the single normative statement); this register's **§4.5** and **OQ-C-06(d-r2)** |
| **OQ-2 — canonical GREEN** | The launcher probes **`/readyz` too** (a second GET over the same `/dev/tcp` fd) and **wrong-version fails fast**; daemon side = version **AND** `composite.ready`. The user ruling's vocabulary names *"unhealthy livez/readyz post-flip"*. The predicate is placed **verbatim** in `AM-P2-4` **and** in **T14 step 1**. | `phase2-plan.md` **§AM-P2-11**; the predicate text in **AM-P2-4** and the **T14** task row |
| **OQ-3 — cap interaction** | **Document-only; no exclusion mechanism.** Commit-closes **never** increment the rollback cap (they are not rollbacks); the cap stays frozen; a breach = **halt-for-human by design**. Documented, noting tonight's wrongful rollback consumed window budget (live at **2/3**). | `phase2-plan.md` **§AM-P2-23** |

### 9.3 The ONE normative absent-only disposition — the five reconciled locations

The council's instruction was to reconcile **all five docs** onto **one** normative absent-only disposition. The reconciled locations:

| # | Doc | Location | What it now says |
|---|---|---|---|
| **1** | `phase2-plan.md` | **§D.2 — the canonical ladder** (restated ONCE) | rung 3 classifies the non-green observation: **BROKEN ⇒ the existing chain fires now**; **AMBIGUOUS ⇒ defer**, unless the **bounded absent-ceiling** predicate holds ⇒ the existing chain |
| **2** | `phase2-plan.md` | **the state/sequence matrix, row 4** | *"bounded: rollback after `SWEEP_DEFER_CEILING` qualifying AMBIGUOUS observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed (AM-P2-10) — the tally persists across launcher boots in `.sweep-defer-<run_id>`"* |
| **3** | `phase2-plan.md` | **§(c) — Never-green crash-loop** | mechanism 1 (the defer ceiling) is **the never-green protection and it is armed** — after the r2 fold; mechanism 2 (the daemon-side accelerator) is unchanged |
| **4** | `decisions.md` | **§4.5 — direction ②** | direction ② has **three** triggers: BROKEN (immediate) · `rollback_requested` (immediate) · **the bounded absent-ceiling**; everything else defers |
| **5** | `decisions.md` | **OQ-C-06(d-r2)** | the leader's adjudication, the two shapes' new statuses (two closed, two operator-bounded), and the tempfail-75 exclusion |
| *(6)* | `architecture-recommendation.md` | **§6(iii)/(iv)** + **§2** | the scenario table's shapes (iii)/(iv) now terminate in the bounded ceiling; the launcher bullet's ceiling-eligibility line is replaced |
| *(7)* | `phase1-plan.md` | out-of-scope note | the "never-green is armed" claim now names its two mechanisms |

**The reconciled sentence (identical in substance in every location):**

> **Never-green is armed by a closed three-path set.** [DEV-FOLD-2026-11-02 — approver iter003 note 2: "exactly two paths" ⇒ the AM-P2-10.5 three-path-set vocabulary; substance identical — the original sentence already enumerated all three.] A **BROKEN** observation (the launcher obtained an HTTP answer and it was wrong — `/livez` non-200 or an unparseable body) ⇒ the **existing** auto-rollback chain fires **immediately**. An **AMBIGUOUS** observation (no answer at all / the previous version / `/readyz` not ready) ⇒ the **existing** chain fires after the **bounded absent-ceiling**: **≥3 qualifying observations AND owner-dead AND `intent_flip` AND never-green-observed**. A `rollback_requested` disposition pre-empts both. **Everything else defers.** The single normative statement is `phase2-plan.md` §AM-P2-10.

**Documented extension (disclosed for the approver).** The leader's predicate names the **absent** class. The canonical never-green shape may also manifest as a **version mismatch** (the daemon answers the *previous* release) or as **`/readyz` not ready** (the daemon is up but degraded) — **neither is an "absence"**. Excluding them would have reproduced the council's REJECT in a narrower form. ⇒ those two sibling ambiguity classes are admitted into the **same** tally under the **same** bound and the **same** three conjuncts. The leader's predicate is honoured **verbatim** whenever the qualifying observations are absent. **The approver may tighten this to the strict reading** (only the `absent` class counts); the plan states both.

### 9.4 The other binding rulings folded in this round

| Ruling / instruction | Disposition | Where |
|---|---|---|
| **OQ-C-11 (already ruled)** — `intent_flip` on **both** sides | **re-affirmed and extended**: the **request** path (T14) is now **also** gated on `intent_flip` — GREEN ∧ ¬`intent_flip` ⇒ **defer + journal** (the R3 hinge). A forged `rollback_requested` + a green probe can no longer commit-close an unauthorized flip. | `phase2-plan.md` §AM-P2-15; pins `P-15` (+negative axis), `P-26`, `P3-PIN-21` |
| **OQ-C-13 (amended by AM-P2-5)** | **re-affirmed**: the counter stays in the launcher-local file; AM-P2-5's *one* clause (the absent exclusion) is amended, the rest stands. | `phase2-plan.md` §AM-P2-5 (clause amended), §AM-P2-10.6 (the superseded text, restated for audit) |
| **OQ-C-22 (one release)** | **confirmed** — v0.28.6 carries phases 1 + 2 + 3 + the cooldown rider. | unchanged |
| **OQ-C-24 (no seal re-pin)** | **confirmed** — the base-red seal tripwire stays visible as disclosed debt. | unchanged |
| **OQ-C-06 premise** | **TRUE** — no 6 h promote-txn `in_flight` reaper in-tree (both councilors grepped). T15 + runbook + the disclosed residual stand. | §OQ-C-06(d-r2) |
| **The three architect gap-closures** | **credited** — AM-P2-1 (history-before-close), AM-P2-5 (counter re-home), AM-P2-4 (`intent_flip` on 2 of 3 decision-makers) all stand; the r2 fold **adds** the absent-ceiling rather than reopening them. | `phase2-plan.md` §AM-P2-10, §AM-P2-11 |

### 9.5 The `v0.28.5` note (carried, not resolved)

`quarantine_cleared_serving_green` **cannot rescue `v0.28.5` itself** — the event fires only while the target **is serving** green, and `v0.28.5` was rolled back off the live symlink (live `current` = `v0.28.4`, `quarantined` = `['v0.28.5']`). The live `v0.28.5` therefore stays quarantined and must be **re-staged** — which is exactly this lane's delivery: **the content re-stages as `v0.28.6`**. The clearance path exists for the *future* wrongful-quarantine shape (a release that is serving green and gets quarantined by a halt), not for the already-rolled-back one.

### 9.6 New ids appended by this fold (for the cross-reference)

| Kind | New ids |
|---|---|
| **Phase 1** | `AM-P1-5` · `T-P1-12` · `L13`/`L14`/`L15` · `M11`/`M12`/`M13` · `AC-P1-17`/`AC-P1-18`/`AC-P1-19` · `SC-8` · `R-P1-13`/`R-P1-14` |
| **Phase 2** | `AM-P2-10`..`AM-P2-23` · `T14`/`T15`/`T16`/`T17` (task-table rows) · `P-13`..`P-34` · `S-11`/`S-12`/`S-13` · `R-13`..`R-17` |
| **Phase 3** | `P3-PIN-18`..`P3-PIN-26` (map rows; `P3-PIN-21` = the three-axis `intent_flip` pin) · `P3-PIN-11a`/`P3-PIN-11b` (the split of the existing `P3-PIN-11` — **the original row is kept**; the split is recorded as its decomposition) · **W2** (defined) |
| **Cross-phase** | the launcher kill-switch **`SWEEP_SERVING_GREEN_ENABLED`** (default `1`) **and** the daemon-side kill-switch **`ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER`** (default `1`, read per tick) — **[PLANNER r3]** both are defined normatively by ONE sentence in `phase2-plan.md` §D.1 (defaults, the runbook, the W1 pre-step) · the new fixture `tests/helpers/serving_probe_fake_listener.py` (modes `wrong-version` / `livez-non200` / `never-answer` / `readyz-red`) · the alert kind **`upgrade_serving_unhealthy_requested`** · **[PLANNER r3]** the daemon-side boot-grace constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, env-overridable, **≥ `HANDBACK_VERIFY_BUDGET_S`**) |

---

## 9.7 [PLANNER r3] FOLD ROUND 3 — F-1 (the surviving boundary defect) + R-1..R-6 — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r3]`; **no existing id — task, pin, decision, OQ, T/P/S/R-number, section number — is renumbered or deleted**. New ids are appended (`AM-P2-24` · `T18` · `P-35` · `S-14` · `R-18` · `P3-PIN-27` · `P3-T13` · `R-P1-15`).

**Verdict context.** The plan council's delta pass returned **APPROVE-WITH-ADVISORIES**, *conditional* on folding **F-1** (one surviving 🔴 critical) plus **REV-R-1**..**REV-R-4** (mandatory text folds); **REV-R-5**/**REV-R-6** are trivial; one carried doc-hygiene item rides with this fold. The **leader confirmed the reviewer's F-1 remediation adjudication** — adopt **both (a)** and **(b)** plus the pin.

**Finding-id namespace (r3).** `REV-R-1` … `REV-R-6` are the **council's delta-pass finding ids, quoted from the review report** — they are **NOT** this plan set's risk ids (this plan set's risk series is the bare `R-n`). The namespace is introduced by this fold precisely so the two cannot collide (`phase2-plan.md` already carries risks `R-1`…`R-17`).

### 9.7.1 F-1 — the root, and the two folds that close it

**The root (stated explicitly).** A composite-present-but-degraded (**warmup**) observation *after* the grace horizon expires writes a **write-once** `rollback_requested` (AM-P2-13) that **stands** even after the daemon turns green; AM-P2-10.5's **path 2** has only the **re-probe** as its gate (it does not re-check `never-green-observed` and does not consult the grace) ⇒ AM-P2-15's *"request + AMBIGUOUS ⇒ execute"* rolls back a **healthy 210–360 s slow boot** at the next restart — with the quarantine, the counter increment, the cooldown and (at live `rollback_window_count` **2/3**) the arming of the 24 h promote halt. **Tonight's class, reproduced by the r2 plan.**

**(a) The closer's NOT-GREEN verdict additionally requires `grace_active == False`.** The `GraceTracker` **never changes `ready`** (`daemon/services/readiness.py:757-760`; the composite's field comment `:404-406`) — *the gate decides to wait; the closer honours the same discipline*. The composite publishes `grace_active` (`:407`, inside `ReadinessComposite` at `:309`), so the conjunct is readable in-process with **no new surface**. ⇒ **degraded-but-grace-held ⇒ DEFER, never `rollback_requested`.**

**(b) The P8 boot-grace horizon is derived from the hand-back budget family.** The r2 plan borrowed `READINESS_GRACE_HORIZON_S = 120.0` (`daemon/constants.py:165`) — that is the readiness service's **pool-busy load** horizon, a different question, and 120 s cannot cover a 210–360 s slow boot. ⇒ the closer's horizon becomes its **own** named constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (env-overridable, default **300**), with the normative derivation **`UPGRADE_CLOSER_BOOT_GRACE_S` ≥ `HANDBACK_VERIFY_BUDGET_S`** (Phase 1's knob, default **300**, `lib.sh:180`) — the two windows must cover the same measured unit-active→serving boot; the closer's horizon may be raised above the hand-back budget but **never lowered below it**.

**The restored invariant (normative, in every doc):**

> **`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.**

**What F-1 does NOT change.** The never-green protection is **untouched** (a genuinely unhealthy serving outside grace still produces the verdict + request and the existing chain still fires); the **path set is untouched** (**BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded** — still no fourth path); path 2's **launcher-side gate is unchanged** (the bypass is closed **at the writer**).

### 9.7.2 The r3 fold map (all five docs)

| Finding | Doc / section | What changed |
|---|---|---|
| **F-1 (a)** | `phase2-plan.md` §AM-P2-24 (new) · §D.1 NOT-GREEN actions · §(c) mechanism 2 · §AM-P2-10.5 | the grace conjunct is added to the closer's NOT-GREEN verdict; **degraded-but-grace-held ⇒ DEFER** |
| **F-1 (b)** | `phase2-plan.md` §AM-P2-24 · §D.1 P8 row · §(c) mechanism 2 | the horizon becomes `UPGRADE_CLOSER_BOOT_GRACE_S` (default **300**, **≥ `HANDBACK_VERIFY_BUDGET_S`**); the borrowed 120 s pool-busy constant is dropped |
| **F-1 — the restored invariant** | `phase2-plan.md` §AM-P2-24 + §D.1 (verbatim) · this register §4.5 | the normative sentence, quoted identically |
| **F-1 — the new pin** | `phase2-plan.md` `P-35` / `S-14` / `T18` / `R-18` · `phase3-plan.md` `P3-PIN-27` / `P3-T13` | the **degraded-but-grace-held ⇒ DEFER** leg joins the both-direction composition set; the never-green leg must use a **not-grace-held** fixture |
| **REV-R-1** | `phase2-plan.md` `P-2`, `P-3`/C5b · `phase3-plan.md` `P3-PIN-06`, `P3-PIN-08`, `P3-PIN-11` · this register §4.5's C5b row | **append-only SUPERSEDE annotations** (no row deleted): only the **BROKEN** class fires immediately; `hard-red` / no-listener are **AMBIGUOUS** ⇒ deferred + counted ⇒ **drive the ceiling pins instead**. Rationale recorded: a tester building from the un-annotated row would hard-redden against the **correct** r2/r3 implementation |
| **REV-R-2** | `phase2-plan.md` §D.6 "Exact firing rule" | the marker `[PLANNER r2 → AM-P2-10.5] superseded — firing rule is the three-path set`; the paragraph is demoted to an audit-trail restatement that **must not be implemented** |
| **REV-R-3** | `phase2-plan.md` §D.1 (new normative paragraph) + `P-30` | **one** sentence defines **both** kill-switches (launcher `SWEEP_SERVING_GREEN_ENABLED`, daemon `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER`), both defaults (`1`), the runbook (T13) and the W1 pre-step; **P-30's `AM-P2-23` citation is corrected** (`AM-P2-23` is the OQ-3 cap-interaction amendment, **not** the kill-switch) |
| **REV-R-4** | `phase2-plan.md` §AM-P2-11 + §T14 step 1 (the two mandated implementer-facing sites) | one sentence at each: a **version-mismatch DISPOSITION is AMBIGUOUS-tallied per AM-P2-10.1, never BROKEN-immediate** (cross-ref `P-27`) — *"fails fast"* is probe-level vocabulary |
| **REV-R-5** | `phase2-plan.md` §AM-P2-11 + §T14 step 1 (mandated) **+ §AM-P2-4** (the third copy) | the duplicated `∧ AND` token is dropped from the canonical GREEN predicate |
| **REV-R-6** | the r3 fold maps in `phase2-plan.md` and `phase3-plan.md` | **recorded, not folded** (approver note): the **seeded-request** pin lives at **`P-13` / `P3-PIN-22`**; **`P3-PIN-25` is the wrong-version listener fixture pin** (the AM-P2-9 probe mutations). The r2 fold's "P3-PIN-25" label is **incorrect** — it must not be used to locate the seeded-request pin. (No in-document mislabel was found in the five docs: every `P3-PIN-25` occurrence correctly names the fixture pin — the mislabel is in the approver's note.) |
| **Carried doc-hygiene** | `decisions.md` §OQ-C-06(d) · `phase2-plan.md` matrix row 4 · `architecture-recommendation.md` §6(iii) | the CEILING=3 timing estimate is corrected to **≈10–15 min** (3 qualifying launcher boots × ≈3.5 min in a `RestartSec=10` + ~200 s dying-boot environment). The earlier **"~30 min"** (decisions) / **"≈30–40 min"** (arch §6(iii)) figures assumed a ~10 min-per-boot cadence — that figure conflated the **burst-budget window** (`BUDGET_WINDOW_S=600`) with the **boot cadence**, and was **wrong** |

### 9.7.3 New ids appended by this fold (cross-reference)

| Kind | New ids |
|---|---|
| **Phase 1** | `R-P1-15` (the hand-back knob's value is the **floor** for the closer's boot-grace horizon) |
| **Phase 2** | `AM-P2-24` · `T18` (task row) · `P-35` (pin) · `S-14` (scenario) · `R-18` (risk) |
| **Phase 3** | `P3-PIN-27` (map row) · `P3-T13` (task row) |
| **Cross-phase** | the daemon-side boot-grace constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, env-overridable, **≥ `HANDBACK_VERIFY_BUDGET_S`**) · the daemon-side kill-switch **`ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER`** (default `1`) |


---

## 10. [PLANNER r4] FOLD ROUND 4 — the fresh approver's iteration-001 REJECT (4 blockers) + 12 carried notes — 2026-11-02

**Fold mode:** additive (Mode 2). Every change below is marked `[PLANNER r4]`; **no existing decision, OQ, pin, task, phase or id is renumbered or deleted**. New ids are appended: **`P3-PIN-28`**, **`P3-PIN-29`**, **`P3-PIN-30`** (consolidated index rows; their phase-2 owners are `P-36`/`P-37`/`P-38`).

**Verdict context.** The fresh approver **REJECTED iteration 001** (2/2 workers converged), with **4 blocking issues**; both verifiers recorded that **all four are doc-level folds over content that already exists correctly elsewhere in the plan set — no new design work is implied**. The leader adjudicated all four **ACCEPTED**, binding this fold: *implementer-facing primary references must never prescribe superseded mechanics* (the same discipline as r3's `REV-R-1`/`REV-R-2`, applied to the sites the council did not annotate). **12 non-blocking notes** rode with the same fold.

**Why this register needed the fold.** The approver's blocker 4 is specifically about **this** document: *"the register-of-record still teaches the SUPERSEDED defer-counter mechanism and the REFUTED 'over-count is the safe direction / CEILING-th encounter executes rollback' doctrine, un-annotated — contradicting §8.2/§9.4/AM-P2-5/AM-P2-10 inside one document, stale side = the primary register table."* ⇒ the **primary register table** was the stale side. That is now closed.

### The r4 fold map (this register's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **BLOCKER 4** | **D.6** taught the superseded defer-counter mechanism + the refuted over-count doctrine | **§2.3, row D.6** | three **SUPERSEDED** markers, append-only: **(1)** the counter's home is the **launcher-local `.sweep-defer-<run_id>` file** (AM-P2-5), not a journal-history count (the history-count was rejected for the cross-entry false positive, the measured O(N²) cost and the missing launcher-side size guard); **(2)** the *"over-count is the safe direction"* doctrine is **INVERTED** — the counter gates the **rollback itself**, so an over-count fires a wrongful rollback, and at the live `rollback_window_count` **2/3** one false rollback **arms the 24 h promote halt**; **(3)** the ceiling's eligibility is the **bounded three-path set** (AM-P2-10.5: BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded; no fourth path). The constant's name, `:=` form and default (3) **stand**; only its meaning changed |
| **BLOCKER 4** | **D.7** said the alert-map entries were *"recommended"* | **§2.3, row D.7** | *"recommended"* ⇒ **REQUIRED** (AM-P2-8), with AM-P2-19's final mapping stated inline: `serving_unhealthy`/`rollback_requested` → the NEW kind `upgrade_serving_unhealthy_requested` (a **request**, not an execution) · `sweep_rollback`/`quarantine` → `upgrade_auto_rollback` (unchanged) · `quarantine_cleared_serving_green` → a NEW kind · `serving_green_commit`/`sweep_deferred` → none |
| **BLOCKER 4** | **OQ-C-13 (c)/(d)** recommended the history-count and stated the refuted doctrine | **§3.2, `OQ-C-13` (c) and (d)** | (c) **SUPERSEDED** — the recommendation is AM-P2-5's launcher-local counter file; the *reasoning* that forced "no new journal field" **stands** (a real capability boundary, verified below). (d) **the "over-count is the safe direction" clause is INVERTED**, with the live 2/3 consequence spelled out and the pin-design advice amended (assert *"≤ CEILING deferrals"* + the three conjuncts individually, never an exact count) |
| **BLOCKER 4** | the **§5.1 one-screen index** rows were un-annotated | **§5.1 rows `P3-PIN-06`, `P3-PIN-08`, `P3-PIN-10`, `P3-PIN-11`** | append-only **SUPERSEDED** annotations carrying the r2/r3 observation taxonomy (only **BROKEN** fires immediately; **AMBIGUOUS** defers + counts), the counter-home correction, the sticky-green rule and the r3 grace split. **Anchor note:** the dispatch named rows 06/08/11; the row that actually carries the defer-counter claim is **`P3-PIN-10`** — it is annotated too, so the index is internally consistent. **No row was renumbered or deleted** |
| **BLOCKER 2** (register site) | **D.2** cited `launcher.sh:1108-1127` as *"commit-close ordering"* — a **fourth** site for the inverted-order prescription, found while folding | **§2.3, row D.2** | the citation is **SUPERSEDED as the model** and the **correct** order is stated inline (AM-P2-1's history-before-close). The `launcher.sh:1112-1117` C-4 `previous`-write ordering pin **still stands** and is cited as such |
| **BLOCKER 2** (register site) | **§4.5 direction ①** stated the mutation order as *"`in_flight`=null, `serving_green_commit` history row"* | **§4.5, direction ① row** | the order is **SUPERSEDED** and the history-before-close order stated; the **third authorization conjunct** (owner-dead-or-heartbeat-stale, AM-P2-2) is added to the launcher's branch description |
| **note (b)** | the `daemon/constants.py:165` anchor was un-annotated | **§2.3, row D.1** | annotated: `READINESS_GRACE_HORIZON_S` is the **pool-busy load horizon the closer does NOT borrow**; the closer's own horizon `UPGRADE_CLOSER_BOOT_GRACE_S` (default 300) is declared in **exactly one place** — `daemon/services/upgrade_journal_sweep.py` beside the other sweep-local constants (`:96-115`; verified `:99`/`:105`/`:112`) — and `daemon/constants.py` is **not** its home |
| **note (g)** | the "~59 min fuse" wording | **§6.2** | the 3540 s figure was **tonight's unconditional auto-rollback age, not a designed fuse** (verified: no 3540 s constant in the tree; the launcher's gate is `SWEEP_STALE_S=600` + a computed age) ⇒ the operative bound is the **≈10–15 min bounded absent-ceiling**; the **launcher/daemon precedence** is stated (the launcher sweeps pre-daemon; the daemon closer normally wins at the first tick after the 600 s gate ≈11 min) |
| **note (h)** | the C-10a fiction exists in **two** copies | **§2.2, row `D-P1-8`** | the second copy is recorded (`promote.sh:255-261`, verified) ⇒ `T-P1-9` must correct **both** copies or the second keeps asserting the fiction |
| **note (l)** | the bounded ceiling vs the commission's literal *"NEVER rollback"* was unsurfaced | **§4.5** (new row) + **§6.3** (new consequence row) | the ceremony gate now surfaces the reconciled sentence — **serving-green ⇒ NEVER rolled back** (absolute) vs **serving-unhealthy ⇒ the existing chain still fires, BOUNDED** — plus the two immediate paths, the cap interaction (a commit-close never increments the cap) and the live **2/3** figure |
| **notes (a), (c), (d), (e), (f), (i), (j), (k)** | phase-local folds | — | folded in their owning files: `phase2-plan.md` (a, c, d, e, i, j), `phase3-plan.md` (f), `phase1-plan.md` (h, k) |

### In-tree re-verification performed at this fold (base `941cb6321`)

| Claim | Verdict | Evidence |
|---|---|---|
| `launcher.sh:1108-1127` is the OLD close-before-history order | **CONFIRMED** | `:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append |
| `lib.sh` has **no** `intent_flip` reader | **CONFIRMED** | `grep -n intent_flip scripts/upgrade/lib.sh` ⇒ **0 hits** (writers at `:1665`; `journal_is_quarantined` at `:1963-1969`) |
| the house history-detail convention is **unquoted** | **CONFIRMED** | `promote.sh:481-482` → `preflight: intentional promote target $VERSION — …`; both writers store the detail as a JSON string (`launcher.sh:443-453`; `upgrade_journal.py:392-407`) |
| `daemon/constants.py:165` is `READINESS_GRACE_HORIZON_S` | **CONFIRMED** | `READINESS_GRACE_HORIZON_S: float = 120.0` |
| the sweep-local constant block is `upgrade_journal_sweep.py:96-115` | **CONFIRMED** | `:99` / `:105` / `:112` — the closer's constant belongs beside them |
| the C-10a arithmetic block is duplicated | **CONFIRMED** | `lib.sh:157-162` **and** `promote.sh:255-261` |
| the launcher's `date -j` sites | **CORRECTED** (phase-3 note (f)) | the executable sites are `:251` (`date -ju -f`, inside the **correctly dispatched** BSD arm of `_js_iso_to_epoch`) and `:474` (the defect); `:238` is a comment; **there is no `:384` comment** — `:384` is `_js_journal_update`'s docstring. The static pin's wording (*"zero **bare** `date -jur` sites outside the BSD arm"*) is **unaffected** |
| the launcher-boot sweep is the decision-maker that fired tonight's wrongful rollback | **CONFIRMED** | the launcher sweep runs pre-daemon (`launcher.sh:1661-1662` vs the daemon child at `:1525`); the daemon-side sweep never adopts or rolls back flipped txns (`upgrade_journal.py:2035-2036`; `:2262-2272`) |

### What this fold does NOT change

- The **never-green protection is untouched** — the path set is still **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**.
- The **owner ruling** is unchanged: serving-green ⇒ stay; rollback is reserved for genuinely unhealthy serving.
- The **frozen quarantine rule** is unchanged — extended by one explicit journaled event, never bypassed.
- The **canonical GREEN predicate** is unchanged in substance; the r4 fold adds **authorization** conjuncts to the commit decision, not new health semantics.
- **No id was renumbered or deleted** in this register.

---

## 11. [PLANNER r5] FOLD ROUND 5 — the fresh approver's iteration-002 REJECT (6 mechanical blockers, narrowed) + 7 same-pass notes + the systematic claim-class sweep — 2026-11-02

**Fold mode:** additive (Mode 2). Every change below is marked `[PLANNER r5]`; **no existing decision, OQ, pin, task, section or id is renumbered or deleted**. **No new id is required** — every r5 fold lands on an existing row or an existing id (the daemon-side authorization conjunct already existed as **P9** / AM-P2-4).

**Verdict context.** Approver iteration **002** REJECTED (narrowed): **B2/B3 fully closed, the notes lane closed, zero fold-regressions** — but the r4 fold stopped at the dispatch-enumerated sites, leaving **six mechanical blockers** and **seven same-pass notes**. Iteration **003** is the approver's pre-committed **NARROW closure check** ⇒ this round is **complete by enumeration**, not by another site-by-site discovery pass.

### The r5 fold map (this register's share)

| # | What folded | Where (this register) | What changed |
|---|---|---|---|
| **BLOCKER 1** | the register's **daemon-side** row omitted the `intent_flip` authorization conjunct | **§2.3, row `D.1`** | the predicate list now reads **P1-P9** and names **P9 = the `intent_flip` authorization conjunct**, with the **Absent P9 ⇒ no-action-this-tick (never commit, never verdict, never request)** rule; the anchors column points at the folded implementer-facing sites (`phase2-plan.md` §D.1's table + `T8` + `P-6`) |
| **BLOCKER 2** | the **fifth** site of the inverted close-before-history prescription | **§3.2, `OQ-C-09` (d)** | *"the commit-close mutation order is the same as the launcher's (`launcher.sh:1108-1127`)"* is **SUPERSEDED** (append-only) — that range is the **OLD close-before-history** order (`:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append); the correct order is **AM-P2-1's history-before-close**. Annotated exactly as row **D.2** was annotated by the r4 fold. The other four sites were `D.2`, `phase2-plan.md` §D.1/§D.2/`T2`, and this register's §4.5 direction ① |
| **BLOCKER 3** | the **pre-r2 firing rule** + the refuted "never served ⇒ unhealthy" rationale | **§2.5, Tension #2** | the firing rule is **SUPERSEDED** (the current rule is AM-P2-10's **bounded absent-ceiling** — ≥`SWEEP_DEFER_CEILING` **qualifying** observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed — not "after 3 deferrals"); the rationale is **refuted in the WRONGFUL-ROLLBACK direction** (never-observed-served is **ambiguity**, not proof of unhealthiness). The reconciled reading: **serving-green ⇒ never rolled back (absolute)**; **serving-unhealthy ⇒ the existing chain still fires, BOUNDED** |
| **BLOCKER 4** | a **stale commission-deferral** + the **superseded precedence** claim | **§3.2, `OQ-C-06` (c)** | the deferral of the run-loop re-sweep hook is **withdrawn** — the hook **IS in this lane** (**AM-P2-7** → **`T15`**, with `AM-P2-10.4` + `AM-P2-17`); and *"the launcher sweep remains the primary rollback engine"* is **SUPERSEDED** — the **daemon closer normally wins** (≈11 min at the first tick past the 600 s staleness gate) and the launcher sweep is the **backstop** (§6.1). **`CEILING = 3`** and the **`:=` form** are **UNCHANGED** |
| **BLOCKER 5** | the **unscoped adopt-parity** assertion | **§5.1, row `P3-PIN-12`** | the shared-polarity claim is **scoped** to the fixtures where all three decision-makers agree; the **GREEN-without-`intent_flip`** fixture is pinned **per decision-maker** (launcher DEFER / daemon no-action / adopt existing-chain). The **never-green untouched**, **launcher purity** and **lock + re-read** assertions are **UNCHANGED**. The same scoping is folded into `phase2-plan.md`'s **R-8** and `phase3-plan.md`'s **`P3-PIN-12`** in the same pass |
| **BLOCKER 6** | the **re-homed mechanism's predecessor** in the plan's most-synthetic reference | (folded in `plan-overview.md` §4 row 2) | *"ceiling 3 via history-count"* ⇒ the counter's home is the **launcher-local `.sweep-defer-<run_id>` file** (AM-P2-5); the launcher's field-creation refusal survives as the *reason* the file was preferred, not as the *mechanism* |
| **note (a)** | `D.4` (the adopt-path row) pointed at nothing | **§2.3, row `D.4`** | the row now points at **`phase2-plan.md` §D.4's (a-i) and (a-ii)** — the two binding divergences (the GREEN-without-`intent_flip` divergence by design; the NOT-GREEN arm's deliberate no-defer/no-grace) — and states the adopt path's **`intent_flip` conjunct** |
| **note (b)** | the **P-8 positive-half** cross-ref | (folded in `phase2-plan.md` `P-8`) | `P-8` is the **positive half** of the §D.3 contract box (v) reader pin; `P-36` / `P3-PIN-28` is the **negative half** |
| **note (c)** | the §D.6 **"Home — journaled history"** paragraph (same class as blocker 3) | (folded in `phase2-plan.md` §D.6) | **SUPERSEDED** — the counter's home is the launcher-local file; the history-count carried cross-entry false positives, a measured **O(N²)** cost and the inverted over-count doctrine |
| **note (d)** | **Appendix A's id inventory** was stale | **Appendix A** | the ranges are **re-derived** from the live files: Phase 1 `L1`..`L5`, `L6a`..`L6e`, `L7`..`L12`, `M1`..`M13`, `AC-P1-1`..`AC-P1-19`, `T-P1-1`..`T-P1-12`; Phase 2 `AM-P2-1`..`AM-P2-24`, `T1`..`T18`, `P-1`..`P-38`, `S-1`..`S-14`; Phase 3 `P3-PIN-01`..`P3-PIN-30`, `P3-T01`..`P3-T13`, `P3-R1`..`P3-R10`, `AC-P3-1`..`AC-P3-12`. **Nothing was renumbered — the ranges were widened to the ids that exist** |
| **note (e)** | **§5.1 omitted** the r2/r3 pins | **§5.1** | the ten rows the r2/r3 folds added to `phase3-plan.md` (`P3-PIN-18`..`P3-PIN-27`) are now present in the register's consolidated table, each cross-referencing its `phase3-plan.md` §4.2.2 row as the authority. **All 30 rows are now present** — the register's summary table is **complete** for the first time |
| **note (f)** | the §4.5 compliance cell's daemon/adopt legs | **§4.5, direction ①** | the daemon and adopt legs now carry the **authorization conjunct** explicitly (the cell previously named the conjuncts for the **launcher** only and said *"same"* for the other two) |
| **note (g)** | the §4.5 compliance cell's adopt-parity scope | **§4.5, direction ①** | the GREEN-without-`intent_flip` divergence is named at the cell (the cell is the register's compliance surface) |
| **note (h)** | the **"recommended" ⇒ REQUIRED** conversion | (folded in `plan-overview.md` §5 P2 bullet) | the overview's alert-kind entry is **REQUIRED**, not a recommendation (AM-P2-8 / AM-P2-19's final mapping) |

### The r5 sweep — this register's hits, classified

| Claim-class | Hit sites in this register | Disposition |
|---|---|---|
| **(a)** predicate/closer restatements missing the P9/`intent_flip` conjunct | **`D.1`** · **§4.5 direction ①** · **`D.4`** | **FOLDED** (all three) · the register's own **OQ-C-11** row already records the ruling (*"the conjunct must be named in the daemon predicates AND T11"*) |
| **(a)** — already correct | **`D.2`** (the launcher leg, with `intent_flip` explicit) · **§9.3's canonical ladder** · **§4.5's restored invariant** (the `grace_active == False` conjunct) · **`OQ-C-11`** (the ruling itself) | **ALREADY CORRECT** — each is either the ruling, a rung that already carries the conjunct, or a lane that carries a *different* conjunct correctly |
| **(b)** close-before-history order claims | **`OQ-C-09` (d)** | **FOLDED** (blocker 2) · **`D.2`** and **§4.5 direction ①** carry the r4 annotations; the r4 fold-map rows are **records** |
| **(c)** pre-r2 firing-rule text | **§2.5 Tension #2** · **`D.6`** · **`OQ-C-13` (c)/(d)** · **§8.2 item 1** · **§5.1 `P3-PIN-10`** | **FOLDED** (Tension #2) · the others already carry the **r4** annotations (three SUPERSEDED markers on `D.6`; the OQ-C-13 supersession; §8.2's "the history-count mechanism is REPLACED") |
| **(d)** history-count counter claims | **`D.6`** · **`OQ-C-13` (c)/(d)** · **§8.2 item 1** | **ALREADY CORRECT** (all r4-annotated) · the register's *own* OQ-C-13 recommendation was superseded by AM-P2-5 and is marked as such at §3.2 and in the r4 fold map |
| **(e)** launcher-primary precedence | **`OQ-C-06` (c)** | **FOLDED** (blocker 4) · §6.1 / §OQ-C-06(d-r2) and the r2 status table already state the corrected precedence |
| **(f)** superseded timing | **§6.2** · **§OQ-C-06(d)** · **§9.7.2's carried doc-hygiene** | **ALREADY CORRECT** (the r4 note (g) marks the "~59 min fuse" wording SUPERSEDED at §6.2; the r3 correction to ≈10–15 min is in place at §OQ-C-06(d)) |
| **(g)** unscoped adopt-parity | **§5.1 `P3-PIN-12`** | **FOLDED** (blocker 5) |
| **(h)** "recommended" alert entries | **`D.7`** | **ALREADY CORRECT** — the r4 fold converted *"recommended"* ⇒ **REQUIRED** with AM-P2-19's final mapping inline; §8.2 item 2 states the hazard (silence, not collision) |

### What this fold does NOT change

- The **never-green protection is untouched** — the path set is still **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**.
- The **owner ruling** is unchanged: serving-green ⇒ stay; rollback is reserved for genuinely unhealthy serving.
- The **frozen quarantine rule** is unchanged — extended by one explicit journaled event, never bypassed.
- The **canonical GREEN predicate** is unchanged in substance; the r5 fold adds **visibility of an existing conjunct**, not new health semantics.
- The **defer ceiling's value and form** are unchanged (`: "${SWEEP_DEFER_CEILING:=3}"`).
- **No id was renumbered or deleted** in this register; **no new id was introduced** — every r5 fold lands on an existing row or an existing id.
