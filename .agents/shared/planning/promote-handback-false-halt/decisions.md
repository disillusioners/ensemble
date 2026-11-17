# Decision Register — `promote-handback-false-halt`

**Commission:** promote-machinery defect fix — **healthy-boot false-halt** (Emperor directive 2026-11-17, "fix it"), aftermath of
promote run `r-20261117-171207-e2f9`.
**Date:** 2026-11-17
**Author:** planner[v2] via plan-creation worker — **decisions consolidation + lane consistency pass** (Mode 2 role: ratify, record, reconcile)
**Status:** **RATIFIED RECORD.** Every decision below is a FINAL dispatcher adjudication, formalized verbatim, not re-opened.
`plan-overview.md` is **not yet authored** by the planner (see §7 correction (j) and §9).
**Plan set:** `.agents/shared/planning/promote-handback-false-halt/`
**Base tree:** worktree `/home/nea/ensemble-src-wt-hbfix` · branch `fix/promote-handback-false-halt` · base `0181dadbf8641c60d7158ad59edfa4a4b36944c8`
**Tip drift at record time:** `origin/latest` = `eca98397d9e0109611764ac418132c9d35afde2c`, **55 commits past the lane base** (verified
`git rev-list --count 0181dadbf..origin/latest` → `55`). The lane KV claim `base == origin/latest` was true at claim time and is **now stale** —
the FINALIZE "tip moved ⇒ STOP and report" rule is live (§9).

**Artifact status:** READ-ONLY pass except this one file. The three phase plans and both research files were **read, never modified**.
No code was written, no commit was made, no script was executed, `/home/nea/agents-ensemble` was not touched (read-only `releases/`
listing + `.env` key-name scan only), and no tag was cut.

---

## 0. Authority chain and how to read this register

### 0.1 Inputs (all read end-to-end before this file was written)

| # | Input | Lines (verified) | Its role here |
|---|-------|------------------|---------------|
| 1 | `phase1-plan.md` | 587 | code-fix design: Candidates A/B/C, containment ladder, restart arm, commit choreography C0–C6, invariants `I-A1…I-E3`, §15 open decisions |
| 2 | `phase2-plan.md` | 988 | tests / rehearsal / negative proof: legs `A0–A7`, `B1–B10`, `C-1…C-8`, `NP-1…NP-11`, restart legs + `R0`, tiers 0/1/2/3/3b, registration `RG-1…RG-7`, §17 open questions |
| 3 | `phase3-plan.md` | 274 | release mechanics: commit-graph options, `P3-D1…D12`, `rollback_safe`, staging sequence, §10 open questions |
| 4 | `research-findings-code.md` | 689 | code evidence base (Q1–Q11 + drift table + NOT-FOUND list) |
| 5 | `research-findings-lane.md` | 171 | incident/lane evidence base (Q1–Q7, plan-input constants) |
| 6 | The dispatch brief's nine rulings | — | **the authority**: every ruling is recorded verbatim in §1, none re-opened |

**ANCHORS-ARE-LEADS.** Every `file:line` in this register was re-read in **this worktree** while writing it (Appendix A logs the greps).
Where a phase plan's citation and the tree disagree, the tree wins and the disagreement is recorded in §7 — the phase files are
**not** repaired in place (commission constraint: this register owns `decisions.md` only).

### 0.2 ID conventions (append-only; nothing renumbered)

- `D-P1-n` — Phase-1 decisions (the plan's own open decisions, §15).
- `D-P2-n` — Phase-2 decisions (the plan's own open questions, §17).
- `D-P3-n` — Phase-3 decisions (the plan's own open questions, §10).
- `L-n` — **ledger-minted** decisions: adoptions and lane-wide dispositions that the phase files *assert as design* but never had to
  adjudicate (A/B/C adoption, the floor-graph ratification, the negative-proof doctrine, the deferred/corrections registers). Minted here so
  the phase files' own namespaces stay free; **never** does an `L-n` overwrite or renumber a `D-*`.
- Phase-3 tasks and criteria are cited as **`P3-T1…P3-T10`** and **`AC-P3-1…13`** — a *citation relabel only*, to stop a collision with
  Phase-1's unprefixed `T1–T12` (finding **CP-3/CP-4**, §8). The phase file itself is untouched.

### 0.3 Reading order

§1 is the register (22 decisions, one row each, with detail blocks). §2 is the **one binding amendment**. §3–§5 are the substantive
ratifications (release graph, negative proof, coalescence). §6–§7 are the registers the coding commission must carry (deferred items,
corrections — so stale claims are never re-imported). §8 is the consistency pass. §9 is the single escalated item. Appendices A–C are
the verification log and the planner's outstanding actions.

---

## 1. Decision register

### 1.0 Index (22 decisions)

| # | ID | Decision (one line) | Binds | Ratifier |
|---|----|---------------------|-------|----------|
| 1 | `D-P1-1` | rc=3 is **not journaled** (INFO `_log` only); rc=124 keeps the legacy byte-identical budget row; rc=4/other → `kind=nonzero_exit` with the true rc; the hard-coded `rc=124` at the reset-failed site dies | `phase1-plan.md` §4.1, §4.3, §15.1 | Dispatcher (FINAL) |
| 2 | `D-P1-2` | Deferral is a **test-only seam** `_GATE_ATTR_DEFER_S` = 5 s **pooled**, *not* a `_numeric_knob` | `phase1-plan.md` §5.4, §15.2 | Dispatcher (FINAL) |
| 3 | `D-P1-3` | **T11 rides** the lane as its own commit; **T12 is deferred** to a named follow-up lane | `phase1-plan.md` §6, §10 (`T11`/`T12`), §15.3 | Dispatcher (FINAL) |
| 4 | `L-1` | **Candidate A adopted** — taxonomy at the three call sites + two pure-string helpers; event kind unchanged | `phase1-plan.md` §4 | Dispatcher (FINAL) |
| 5 | `L-2` | **Candidate B adopted** — containment ladder R1–R4 as a **second accept path** + pooled deferral; advisory rescue rejected-for-verdict / kept-as-forensics | `phase1-plan.md` §5 | Dispatcher (FINAL) |
| 6 | `L-3` | **Candidate C adopted** exactly as scoped (six touchpoints, readers unchanged) **+ T11** | `phase1-plan.md` §6 | Dispatcher (FINAL) |
| 7 | `L-4` | **Restart arm adopted** — minimal 7-class journaling, kind `halt`, `|| true`, 5 pre-journal-init exits allowlisted | `phase1-plan.md` §7; `phase2-plan.md` §10 | Dispatcher (FINAL) |
| 8 | `D-P2-1` | **Tier-3 rehearsal routed to the tester gate** — `NP-7b` is a declared planning gap; lib-level `NP-7a` is the compensating pin; §11.4 is the hand-off spec | `phase2-plan.md` §11.4, §17 (`OQ-P2-1`) | Dispatcher (FINAL) |
| 9 | `D-P2-2` | The **5 pre-journal-init silent restart exits are ACCEPTABLE**; `R0` pins the allowlist; the non-journal stderr-audit channel joins the deferred register | `phase2-plan.md` §10.1, §17 (`OQ-P2-2`) | Dispatcher (FINAL) |
| 10 | `D-P2-3` | **NP-6 gated NOW** — Phase-1's **R2 rung gains SEGMENT-AWARE matching**; `NP-6` becomes a hard pin; `M-NP1b` must redden it | **AMENDMENT** — `phase1-plan.md` §5.3 (see §2) | Dispatcher (FINAL) |
| 11 | `D-P2-4` | **Darwin `NP-11` DROPPED** — declared parity gap in the evidence pack (follows the `T12` deferral) | `phase2-plan.md` §9.1, §17 (`OQ-P2-4`) | Dispatcher (FINAL) |
| 12 | `D-P3-1` | **Rebase-vs-merge DEFERRED TO THE LEADER** (house FINALIZE). Mechanics are agnostic; only `M`'s identity / the stamp value changes. **Do not decide it here.** | `phase3-plan.md` §10 (`P3-Q1`), `P3-D11` | Dispatcher → **leader** (§9) |
| 13 | `D-P3-2` | **Scripts-pin advance is OPERATOR-OWNED**, recorded as a ceremony precondition, target = the release tag | `phase3-plan.md` §4.4, `P3-D6`, §10 (`P3-Q2`) | Dispatcher (FINAL) |
| 14 | `D-P3-3` | **CHANGELOG disclosure set CONFIRMED** — five items; `plan-overview.md` mirrors the same set | `phase3-plan.md` §10 (`P3-Q3`), §6 (`P3-T4`) | Dispatcher (FINAL) |
| 15 | `L-5` | **Floor graph ratified**: `P3-D1` — stamp/floor = **merge commit `M`**, re-pin rides bump commit `B`; `F→M→B→T` | `phase3-plan.md` §4.1–§4.3, §5 (`P3-D1`) | Dispatcher (FINAL) |
| 16 | `L-6` | **`rollback_safe` = explicit `1`** with the delta-empty rationale; rollback returns the defect until re-promote | `phase3-plan.md` §4.5, `P3-D5` | Dispatcher (FINAL) |
| 17 | `L-7` | **Version slot re-derived on three axes** at bump time (currently `v0.31.1`; never hardcoded) | `phase3-plan.md` §5 (`P3-D4`), `P3-T1` | Dispatcher (FINAL) |
| 18 | `L-8` | **No-promote boundary upheld** (`P3-D7`): the lane ends ceremony-ready; `promote.sh` recorded, never executed; `current` not flipped | `phase3-plan.md` §1, §5 (`P3-D7`), `P3-T10` | Dispatcher (FINAL) |
| 19 | `L-9` | **Negative-proof doctrine adopted** — "a genuinely wrong release must still halt"; every accept-path widening ships with its reddening counter-mutation | `phase2-plan.md` §9; `phase1-plan.md` §5.7, §14 | Dispatcher (FINAL) |
| 20 | `L-10` | **Coalescence closure recorded** for board item `wi-d3579d7c` #1 — this lane removes the producer; the closer is untouched | `phase1-plan.md` §8 | Dispatcher (FINAL) |
| 21 | `L-11` | **Deferred register adopted** (T12, NP-11, non-journal restart-audit channel) | §6 below | Dispatcher (FINAL) |
| 22 | `L-12` | **Corrections register adopted as authoritative** over stale commission/research claims | §7 below | Dispatcher (FINAL) |

**Nothing in this register weakens a phase plan's hard constraints.** Where a decision *narrows* the plan (`T12`, `NP-11`), the narrowing is
explicit and its compensating coverage is named (§6).

---

### 1.1 Phase 1 — code-fix design

#### `D-P1-1` — rc=3 policy · **ADOPTED**

**Decision.** No journal row for rc=3; a `_log` INFO line only.
**Phase-2 pin (binding, recorded verbatim from the ruling):** the INFO line **exists**, never contains `"exceeded budget"` / `"timeout"`, and
**no `kind=budget` row appears for rc=3**.
Branch semantics (unchanged by the narrowing):

| rc | class | disposition |
|---|---|---|
| `124` | `budget` | legacy WARN + `kind=budget … rc=124` — **byte-preserved** |
| `3` | `is-active: not-active` (inactive *or* activating) | **no journal row**; `_log` INFO; treat as not-active (behaviour identical) |
| `4` | `is-active: unknown` | `kind=nonzero_exit` + the **true** rc |
| other ≠0 | `nonzero_exit` | `kind=nonzero_exit` + the **true** rc |

The hard-coded `rc=124` at `lib.sh:6337-6339` is **killed** (verified: that site prints `rc=124` unconditionally today).
**Rationale.** Real `systemctl is-active` returns rc 3 for *every* inactive unit — i.e. on every healthy hand-back. Journaling it would put a
`subprocess_wait_timeout` row in the forensic surface of every clean ceremony, and the incident proved how expensive a mislabelled routine
row is. Honesty is preserved at INFO, where "not-active is the expected answer" belongs. **Rejected alternative:** journal
`kind=nonzero_exit` for rc=3 as well (would force Phase 2's healthy-boot assertion to be re-worded to "no `kind=budget` rows", and would
re-pollute every healthy ceremony's forensic surface).
**Binds:** `phase1-plan.md` §4.1 (refinement rationale), §4.3 (three sites), §15.1. **Phase-2 pins:** legs `A3`/`A6` with the policy-pinned
assertion (`phase2-plan.md` §6.1, mutation `M-A3b`).

#### `D-P1-2` — deferral exposure · **ADOPTED**

**Decision.** `_GATE_ATTR_DEFER_S` = **5 s pooled**, implemented as a **test-only seam** (env-visible override, following the
`_COOLDOWN_DATE_UNAME` observable-override precedent at `lib.sh:494-497`), **not** a `_numeric_knob`, **not** exported.
**Rationale (three legs, all zero-churn).** (i) **No new public knob surface**; (ii) therefore **no C-10a numeric churn** and **no
source-textual ceremony-wallclock change** (`tests/test_ceremony_wallclock.sh:160-186` stays green with zero edits); (iii) **no daemon-side
closer coupling** — the closer's grace floor `max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)` is untouched because no budget
number moves.
**Pooled, not per-call** (a property of the design, restated because the mutation `M-B-defer` targets exactly it): a per-call 5 s defer
inside a 2 s poll loop would let a pathological boot consume 300 s in ~25 iterations instead of ~150 — a 6× slowdown of a halt we must still
issue. The pool bounds **total** patience to 5 s per process.
**Rejected alternative:** promote to a `_numeric_knob` + C-10a numeric update (more operator-visible, but more churn and a wall-clock delta
the ceremony pack would have to absorb).
**Binds:** `phase1-plan.md` §5.4, §15.2. **Phase-2 legs:** `B-defer-a`/`B-defer-b` (`phase2-plan.md` §7.5), mutation `M-B-defer`; `A7(iii)`
asserts exactly the "env-visible, not exported, not a knob" shape.

#### `D-P1-3` — optional items · **T11 IN / T12 DEFERRED**

| Item | Ruling | Content |
|---|---|---|
| **T11** | **RIDES the lane as its own commit** | `promote.sh:1051-1056` — the *rollback* hand-back halt gains the `HB_ERR` reader (same accessor, rm-after-read) so a rollback hand-back halt stops saying nothing about why. **Verified:** the block is exactly `:1051-1056` (`if ! restart_via_launcher; then … exit 1; fi`), journal row at `:1053-1054`, `exit 1` at `:1055` — the dispatch's range is precise. |
| **T12** | **DEFERRED to a named follow-up lane** | Darwin `_flip_verify_portattribution` widening (`lib.sh:6996`). Rationale: the incident is **Linux/unit-anchored**; rungs **R3/R4** already give mode-general accept paths; widening without a **provable Darwin stub leg** exceeds the ceremony-critical minimum. Compensating coverage: `B7` (Darwin/Plan9 arm selection via the `uname` stub) still runs; the Linux fix is the **certified** one (`phase1-plan.md` §12 "secondary"). |

**Binds:** `phase1-plan.md` §6 (T11 touchpoint), §10 (`T11`/`T12`), §15.3; commit `C4` carries T11.
**Phase-2 legs:** `C-7` gains the rollback template row; the Darwin legs (`B6`/`B7`) stay as written; `NP-11` is dropped per `D-P2-4`.

---

### 1.2 Adoption ratifications (Candidates A / B / C + restart arm)

#### `L-1` — Candidate A adopted: **honest rc taxonomy at the call sites**

**One-line design.** Keep `_run_bounded` **byte-unchanged** (`lib.sh:4856-4882`; its only legitimate remap is `137|143 → 124`); put the
taxonomy where the child rc is *interpreted* — the three dishonest sites (`:6332-6339` reset-failed, `:6356-6360` is-active preflight,
`:6695-6703` scope arm) — via two **pure string** helpers (`_bounded_rc_kind`, `_bounded_rc_reason`) next to `:4883`; refine rc=3 per
`D-P1-1`.
**Event kind unchanged.** The journal **event** stays `subprocess_wait_timeout` (informational only — not in `_TERMINAL_EVENTS`, verified at
`upgrade_journal.py:2529-2557`); only the row's `kind=` token changes. Renaming the event would drag a daemon-side label edit
(`upgrade_tools.py:1317`), a metrics-catalogue edit, and pinned-test churn for zero forensic gain. The naming debt is mitigated by one comment
per changed site ("the event kind names the bounded-wait *family*; the row's `kind=` names the *outcome*").
**Rejected.** (i) teaching `_run_bounded` systemctl semantics — poisons every other caller (`stop_via_stop_script` `:5281-5321`,
`_df_systemctl` `:7688-7702`); (ii) renaming the event kind — blast-radius expansion this lane must avoid.
**Binds:** `phase1-plan.md` §4.1–§4.3. **Tests:** `A0–A7` + `M-A*` (`phase2-plan.md` §6).

#### `L-2` — Candidate B adopted: **containment ladder as a second accept path** (the load-bearing fix)

**One-line design.** `_gate_serving_attribution` keeps **Layer 1 first and unconditional**; the ladder is consulted **only when Layer 1 found a
non-match** — so the all-owned common case stays byte-identical (no extra `systemctl show`, no log change, no probe-count change), while the
shapes that refuse today gain a containment answer:

| Rung | Predicate | Cost / portability |
|---|---|---|
| **R1** | holder ∈ argv/cwd-anchored owned set (already computed) | zero extra cost |
| **R2** | unit mode, Linux: holder `== MainPID` **or** `/proc/<holder>/cgroup` last-line contains the unit ControlGroup — **AMENDED to segment-aware matching, §2** | 2 bounded `systemctl show` probes, resolved once per gate invocation (`_gate_attr_unit_ctx`) |
| **R3** | bounded ppid walk (depth ≤ 12) into the owned **set** (`_supervision_pid_ancestor_of`) | POSIX `ps`; also serves Darwin |
| **R4** | Darwin launchd job lineage; **empty job pid while a holder serves ⇒ `1`** (dual-supervisor refusal preserved) | bounded `launchctl` |

Verdicts: any `1` ⇒ refuse with **today's detail prefix byte-for-byte** (+ a ladder suffix); all `0` ⇒ `_log` `ACCEPTED …` and continue to
Layer 2; no `1` and some `2` ⇒ the **pooled deferral** (`D-P1-2`), exhaustion ⇒ distinct `… UNATTRIBUTABLE within <n>s … failing closed` ⇒ rc 1.
**Advisory rescue: rejected for the verdict, kept as forensics.** The in-process `SUPERVISION_*` channel is already reliable and strictly better
than a stamp read; the daemon `supervision_boot` advisory is **racy by contract** (may not be journaled at ~38 s in) and would put a
probabilistic input into a fail-closed conjunct.
**Root cause stated in code terms (unchanged).** G1: the strictly-more-permissive containment signal is consulted only *after* the weaker one
has refused; G2: the verdict is a single instant census with no re-probe, while `_flip_verify_port_attribution` already learned the
timing lesson (D-1: "the attribution requirement is RIGHT; the TIMING was the defect").
**One-fix-many-surfaces is deliberate** (and must be tested): the shared gate also covers `gate_livez`/`gate_readyz`/`gate_version`(soak) —
legs `B-parity`/`NP-9`.
**Rejected.** (i) pure reorder (loses the zero-cost fast path, makes every byte-level pin about the accept path suspect); (ii) LISTEN-grace as
*the fix* (an inherited-fd child never becomes "owned" ⇒ burns the budget then halts; grace is correct only for the *unattributable-yet*
class).
**Binds:** `phase1-plan.md` §5 (all subsections), §5.7 protection matrix. **Tests:** `B1–B10`, `B-order`, `B-parity`, `B-halt` +
counter-mutations (`phase2-plan.md` §7.9).

#### `L-3` — Candidate C adopted: **stash the systemd-lane diagnosis** (exactly as scoped) **+ T11**

**One-line design.** Reuse the existing plumbing (`HANDBACK_LAST_ERROR` + `$$`-keyed `HANDBACK_ERROR_FILE`, `lib.sh:5592-5614`; **readers
unchanged**); add one writer wrapper `_handback_stash()` and six touchpoints: **C-1** clear on entry (a stale seed can never mask this failure),
**C-2** reset-failed both arms, **C-3** is-active both arms (including the benign rc 3 — it is the *context* of a later failure; last write
wins), **C-4** the start arm's already-captured first stderr line (highest-value loss today), **C-5** the verifier's reason via a new
`HB_VERIFY_REASON` **plus an explicit file write from inside the verifier** (it runs in `$( )`), **C-6** clear on success. Plus **T11**
(rollback halt reader).
**Why the file channel matters.** It is the only channel that survives `$( )` — the MAJOR #1 regression class; `I-C5`/mutation `M-C4` pin it.
**Binds:** `phase1-plan.md` §6. **Tests:** legs `C-1…C-8` + `M-C1…M-C4` (`phase2-plan.md` §8); regression obligation: `§N` launchd-stash
nodes stay green **without edits**.

#### `L-4` — Restart arm adopted: **minimal 7-class journaling**

**One-line design.** Honest labels in the scope arm come with Candidate A (site 3). Additionally, at each of the seven `_warn`-only
exit-78 refusal sites (`restart.sh:122, 136, 148, 155, 161, 165, 173` — exits at `:124, :137, :149, :156, :162, :166, :174`, all verified)
append **one `halt` row** before the exit: `journal_history_append halt "restart executor run_id=…: refusal class=<slug> — <cause>; exit 78,
NOTHING armed/executed" || true`.
**Why `halt`, not `refusal`.** The same script already journals `halt` for its lock-adoption refusals (`:180`, `:186`), and `halt` needs no
daemon-side label; `refusal` is produce-once by `upgrade_tools._refusal` — a second writer with different semantics would be a contract change.
**Why `|| true`.** At the python-floor site the writer *is* python3 (verified: `journal_history_append` → `_splice_journal` → `journal_splice.py`
→ python3, `lib.sh:1935-1950`), and at the TORN site the journal is torn: best-effort is the only honest promise. The row is **visibility**,
never a gate. Documented side effect (accepted): `halt` ∈ `_TERMINAL_EVENTS`, so it lands in the restart-kind wake superset — conservative
direction (notify, never miss).
**Allowlist (per `D-P2-2`):** 5 sites remain silent — `:82`, `:96`, `:104`, `:108` (before `journal_init` at `:143`) and `:145`
(`journal_init` itself failed); plus the post-adoption advisory `:192`. **`R0` pins the 6-entry allowlist as an exact line set.**
**Explicitly NOT in scope:** persisting the arm's `pending_op`, the `lock_acquire` `to_thread`, and the boot-sweep default (board sub-items
2–4).
**Binds:** `phase1-plan.md` §7; `phase2-plan.md` §10.1–§10.2.

---

### 1.3 Phase 2 — tests / rehearsal / negative proof

#### `D-P2-1` — Tier-3 rehearsal · **ROUTED TO THE TESTER GATE**

**Ruling.** `NP-7b` (the promote-level end-to-end halt proof) is a **declared planning gap**, not a Phase-2 deliverable. Lib-level **`NP-7a`**
is the compensating pin. `phase2-plan.md` **§11.4** is the hand-off spec for the coding commission's tester cycle: `TARGET=sandbox`, a
`mktemp` `INSTALL_DIR`, an ephemeral high port (never 9797/7979/8079/8088/5432), PATH-first stubs, **no** `ENSEMBLE_UPGRADE_LIVE=1`,
`ENSEMBLE_SELF_ENV=sandbox`, no DB dump required.
**Honest consequence (must appear in the evidence pack).** The promote-level halt is proven **structurally** + by the existing template pin
(`test_supervision_stop_handback.sh` §N3) + the caller contract `B2b`, **not** by an executed `promote.sh` in this lane.
**Binds:** `phase2-plan.md` §11.4, `P2-T15`, §17 `OQ-P2-1`.

#### `D-P2-2` — 5 pre-journal-init silent exits · **ACCEPTABLE**

**Ruling.** The five silent exits — `:82`, `:96`, `:104`, `:108` (all before `journal_init` at `:143`) and `:145` (`journal_init` itself
failed) — are **acceptable**; `R0` pins the allowlist as the visible record. The **non-journal stderr-audit channel** joins `T12` in the
deferred register (§6).
**Clarification recorded (so the count never drifts).** The `R0` allowlist is **6 entries**: those 5 **plus** `:192` (post-adoption advisory —
a journal append would race the very lock write that just failed). `phase2-plan.md` §10.1 states both numbers correctly; this register records
them together so neither reading is lost.
**Visibility that exists today:** `_warn` text on stderr and the client log (`Running as unit … code=exited/status=78`). That is the honest
limit of the landed design; `R0` makes it **explicit and pinned** rather than invisible.
**Binds:** `phase2-plan.md` §10.1 (`⚠️` on the `:122` class + residual), §17 `OQ-P2-2`; `P2-T11` (`R0` + `R-PY` two-sided).

#### `D-P2-3` — NP-6 · **GATED NOW → binding amendment to Phase 1 §5.3** → see **§2**

#### `D-P2-4` — Darwin `NP-11` · **DROPPED**

**Ruling.** `NP-11` (Darwin variant of `NP-4`, written only if Phase-1 took optional `T12`) is **dropped**; the parity gap is **declared** in
the evidence pack (`phase2-plan.md` §13 item 8 / §9.4 pattern). It follows mechanically from `D-P1-3`'s `T12` deferral.
**Residual coverage that remains (so "dropped" is not "uncovered"):** `B7` proves the **arm-selection** logic on this host via the `uname` stub
(Darwin ⇒ no `/proc`, no ControlGroup probe; Plan9 ⇒ fail-closed), and `B6` proves the launchd **rung** (`R4`) three ways including the pidless
job refusal. What is *not* proven is a Darwin-shaped **legacy** `_flip_verify_portattribution` after a T12 widening — which is precisely the
deferred work.
**Binds:** `phase2-plan.md` §9.1 (`NP-11` row), §17 `OQ-P2-4`; `phase1-plan.md` §15.3.

---

### 1.4 Phase 3 — release mechanics

#### `D-P3-1` — rebase vs merge · **DEFERRED TO THE LEADER** → recorded as the lane's single escalated item in **§9**

#### `D-P3-2` — scripts-pin advance owner · **ADOPTED (operator-owned, ceremony precondition)**

**Ruling.** The scripts-pin worktree advance is **operator-owned**, recorded as a **ceremony precondition**, with the **target = the release
tag**.
**Why it is load-bearing (not bookkeeping).** The ceremony executes the **pinned** tree, not the payload: the daemon resolves
`ENSEMBLE_UPGRADE_SCRIPTS_DIR` first (verified: `upgrade_tools.py:2050`; live `.env:107` pins the scripts-pin worktree) and the live install has
**no** `scripts/` at its root. So staging a release does **not** deploy the fixed `lib.sh`/`promote.sh` into the executing path. Detached at
the tag `T` satisfies three things at once: the fix executes, the stamp equals the new floor, and the next ceremony's ancestry check is
satisfied.
**Loudness if skipped:** a stale pin refuses **pre-nonce**, so it is recoverable by advancing the pin — but the *stage* stays green either way,
which is why the precondition must be named in the tag annotation, the stage journal and the handoff report.
**Binds:** `phase3-plan.md` §4.4, `P3-D6`, `P3-T9`-adjacent (handoff), §10 `P3-Q2`.

#### `D-P3-3` — CHANGELOG disclosure set · **CONFIRMED (five items)**

The entry (and `plan-overview.md`) carry exactly:

1. **fix summary** — healthy-boot false-halt (rc honesty + containment ladder + diagnosis stash + restart visibility);
2. **floor/stamp re-pin** — both constants → `M`;
3. **`rollback_safe` adjudication** — explicit `1`, delta-empty;
4. **no-promote disclosure** — this lane stages only; the promote is the Emperor's ceremony;
5. **scripts-pin precondition** — operator-owned advance to `T`.

**Binds:** `phase3-plan.md` §10 `P3-Q3`, `P3-T4` (CHANGELOG section), §6 `P3-T5` (tag annotation carries (2)(3)(4)(5)).

---

### 1.5 Floor-graph / rollback / version slot / boundary (ratified mechanics)

#### `L-5` — floor commit-graph · **`P3-D1` RATIFIED** — see **§3**

#### `L-6` — `rollback_safe` · **explicit `1`** — see **§3.3**

#### `L-7` — version slot · **re-derive on three axes** — see **§3.4**

#### `L-8` — no-promote boundary · **upheld**

**Ruling.** The lane ends at a **ceremony-ready staged release**. `promote.sh` is **recorded, never executed**; the `current` symlink is not
flipped; the live journal's promote fields stay untouched (`P3-D7`). The recorded promote argv lives only in the handoff report and the tag
annotation; the lane's exit criterion states `promote.sh` was never run; the live fence (AC-P3-12) is the detector.
**Rejected.** A "dry" promote against live; flipping `current` as a smoke test — both are unauthorised live changes (`P3-R7`).
**Binds:** `phase3-plan.md` §1, §5 (`P3-D7`), §6 `P3-T10`, §7 `AC-P3-13`.

**Additional Phase-3 mechanics ratified as written** (no dispatch override; recorded so the commission treats them as binding):

| ID | Mechanic | Status |
|---|---|---|
| `P3-D2` | the re-pin is a **targeted 2-line edit inside the bump commit**; a separate `fix(floor):` commit is the **fallback** only, if the coverage gate demands it | ratified |
| `P3-D3` | exactly two edited lines (`lib.sh:95` unquoted; `upgrade_tools.py:4145` double-quoted with spaces); **prose untouched** (extraction regexes are format-exact) | ratified |
| `P3-D8` | stage from a **detached** worktree at `T`; push order `HEAD:latest` **then** the tag | ratified |
| `P3-D9` | **rebuild the binary at the tag**; `--skip-build` is **not** used (the floor constant is compiled into the frozen binary — a pre-bump binary would disagree with the manifest) | ratified |
| `P3-D10` | **one 7-file house commit** `chore(release): bump version to <ver>`; `bump_version.py patch` derives the version + makes the 4 mechanical edits, then `git reset --soft HEAD~1` and re-commit **all 7 explicit paths** (its own commit is 4-file, message `! New version`, and its `uv sync` would materialise a `.venv` in this lazy worktree) | ratified |
| `P3-D12` | the release version is **`v`-prefixed** in `VERSION=`, the tag, and the manifest | ratified |
| `P3-D11` | merge **`--no-ff`** onto `latest` with the drift cleared first — **mechanics agnostic pending `D-P3-1`** (only `M`'s identity changes) | ratified-no-override |

---

### 1.6 Lane-wide dispositions

#### `L-9` — negative-proof doctrine — see **§4**

#### `L-10` — coalescence closure — see **§5**

#### `L-11` — deferred register — see **§6**

#### `L-12` — corrections register — see **§7**

---

## 2. ⚠️ BINDING AMENDMENT — `D-P2-3`: Phase-1's **R2 rung gains SEGMENT-AWARE matching**

> **This is the ONE place the ratified Phase-1 design is MODIFIED after the fact.** Everything else in this register formalizes what the phase
> plans already assert; this one **changes a predicate** in `phase1-plan.md` §5.3 row 2.

### 2.1 What changes

| | Before (`phase1-plan.md` §5.3, R2) | After (`D-P2-3`, binding) |
|---|---|---|
| Predicate | holder `= MainPID` **or** `/proc/<holder>/cgroup` last-line **contains** the unit ControlGroup (**raw substring**) | holder `= MainPID` **or** the cgroup last-line matches the ControlGroup at a **segment boundary** — i.e. `pcg == cg` **or** `pcg` contains `"$cg/"` (the match must be followed by `/` or end-of-string) |
| Failure mode | a sibling/nested cgroup whose path **extends** ours (`/system.slice/ensemble.slice2/...` vs ours `/system.slice/ensemble.slice`) is **accepted** — raw substring over-accepts across slice-hierarchy prefixes | a prefix-extension cgroup is **not** matched ⇒ rung answers `1` ⇒ refuse |

**Why the amendment is right.** The raw substring test is exactly today's `lib.sh:8119-8135` shape transplanted into a new rung; copying it
would import the over-accept into the *widening* path, where the accept-side consequence is new. Segment-awareness costs one comparison and
removes the class rather than documenting it.

### 2.2 Consequences (all binding)

1. **`NP-6` becomes a HARD PIN.** `phase2-plan.md` §9.1 `NP-6` was written two-armed (arm (a) substring retained ⇒ advisory; arm (b)
   segment-aware ⇒ refusal). **Arm (b) is now the contract.** The leg asserts the **refusal** and prints the arm for the evidence pack.
2. **`M-NP1b` must redden `NP-6`.** The mutation "make the cgroup test unconditional / permissive (`case "$pcg" in *) ;;`)" must turn `NP-6`
   red. `NP-6` without a reddening mutation is a failed leg (`phase2-plan.md` §9.2, `SC-1`/`SC-2`).
3. **Implementation touchpoints.** `P3`-unrelated: Phase-1 **`T5`** (`_gate_attr_containment`, rung R2) and **`T4`** (`_gate_attr_unit_ctx`,
   which resolves `_GA_UNIT_MAINPID` + `_GA_UNIT_CG` once per invocation and is the natural home of the matcher). The equality arm
   (holder `== MainPID`) is unaffected.
4. **`phase1-plan.md` §5.3's body text is now STALE at row 2.** The phase file is **not edited** (commission constraint), so the ledger of
   record is **this register**. The coding commission implements §2.1, not the phase file's substring wording. This is the
   `plan-body-ledger-agreement` hazard in the precise form it warns about: *body ≠ ledger; the ledger wins; predicate strings must match
   character-for-character.*
5. **Scope of the amendment — R2 ONLY.** The pre-existing **Layer 2** `_gate_attr_unit_lineage` (`lib.sh:8100-8137`) keeps its substring
   test **byte-identical** (the common path must not change — `I-B2`/`B-order`). This is sound, not an inconsistency:
   - a **non-owned** holder can reach Layer 2 only via a ladder `0` acceptance (any `1` refuses first);
   - when `nonmatch` is **empty**, every holder is already owned, so Layer 2's substring cannot convert a *foreign* holder into an accept.
   Therefore the residual over-accept surface is exactly the ladder's acceptances — which is precisely what `NP-6` now pins.
6. **The documented P12 residual is untouched** by this amendment: "a second supervisor **inside** our unit cgroup" remains accepted (systemd
   only starts it), an operator-hostile shape, unchanged risk class (`phase1-plan.md` §5.7 P12).

### 2.3 Where it is bound

`phase1-plan.md` §5.3 (amended), §14 `I-B4`/`I-B12`; `phase2-plan.md` §9.1 (`NP-6`), §9.2 (`M-NP1b`), §17 (`OQ-P2-3` — **answered: arm (b)**).

---

## 3. Floor commit-graph, `rollback_safe`, and version-slot ratification

### 3.1 The chicken-and-egg, restated exactly (why two commits minimum)

The pair must name **one commit** (byte-equal, `lib.sh:748-751`), 40-hex (`:740-742`), resolvable in the release tree (`:744-746`), an
**ancestor of the built tree HEAD** (`:783-787`), and **containing every shipped executor-family path** (`stage.sh:817` →
`executor_family.sh:315-331`; **17 paths verified** via `bash scripts/upgrade/executor_family.sh --paths | wc -l` → `17`). Because this lane
edits `lib.sh`/`promote.sh` — both executor-family members — the naming commit is a commit that edits the file carrying the stamp. A commit
cannot contain its own sha (hash fixed-point), so the value must name an **existing** commit ⇒ the fix lands first, the re-pin rides a **later**
commit.

### 3.2 `L-5` — `P3-D1` RATIFIED: value = **`M`**, re-pin rides **`B`**, graph **`F → M → B → T`**

```
  fix/promote-handback-false-halt
        │  F…   Phase-1 code fixes (scripts/upgrade/lib.sh, promote.sh, restart.sh)
        │  F'…  Phase-2 tests/packs (tests/**, .agents/tester/PACKS.md)
        ▼  merge --no-ff  (into latest) ──►  M   ← THE FLOOR/STAMP VALUE
        ▼  "chore(release): bump version to <v>"   (7 files, includes the 2-line re-pin)  B
        ▼  git tag -a v<v>                                                                  T
        ▼  detached stage worktree at T → FE rebuild → sidecars → stage.sh live
```

| Predicate | Holds at `M` |
|---|---|
| 40-hex (`lib.sh:740`) | ✅ (a merge commit sha) |
| resolves in the release tree (`:744`) | ✅ |
| `stamp == floor` (`:748`) | ✅ (both set to `M` in `B`) |
| floor ancestor of built HEAD (`:783`) | ✅ (`M` is `B`'s parent ⇒ ancestor of `T`) |
| family coverage over 17 paths (`stage.sh:817`) | ✅ (all 17 exist at `M`) |
| arm-time enforcement (`upgrade_tools.py:4135-4179`) | ✅ floor `= M` ⇒ the next ceremony **demands** the fix |

**Rejected alternatives (enumerated, as ruled):**

| Option | Shape | Why rejected |
|---|---|---|
| **B** | re-pin names "whatever `B`'s parent is" | a *positional* convenience that silently becomes wrong the moment anything lands between `M` and `B` — and `origin/latest` is 55 commits ahead of the base, so other lanes are demonstrably landing. The house wording is a **named** commit, never a positional one. |
| **C** | re-pin names the tag `T` | **impossible** — `T` cannot contain `T`'s sha (self-reference). (Naming the tag's *target commit* is legitimate; naming the commit that carries the re-pin is not.) |
| **D** | keep `1aa0065eb`, ship the fix | **mechanically invisible at stage time** (every stage predicate passes for `1aa0065eb`) but semantically fatal: the arm-time guard's purpose is "a tree that does not contain the fix commit object cannot contain the fix". With a stale floor, a **pre-fix** scripts tree still arms; the release ships the fix while the guard stops demanding it. `stage.sh` will **not** catch it ⇒ needs the explicit release-time assertion `P3-T7`/`AC-P3-8`. |
| **E** | `REV` and `FLOOR` diverge | refused **by contract** (`lib.sh:748-751`, "the two travel together"). |

**Anti-D guard is mandatory** (`phase3-plan.md` `P3-T7`, `AC-P3-8`): before staging, assert the shipped pair **equals `M` specifically** and
run the two verdict helpers + the coverage verdict against the stage tree — do **not** rely on `stage.sh` to catch a stale-but-consistent pair.

### 3.3 `L-6` — `rollback_safe` = **explicit `1`** (delta-empty rationale)

- The **unset** derivation path greps the whole migration history (110/133 files match `DROP TABLE|DROP COLUMN`) ⇒ `contains_contract_phase=true`
  ⇒ the derivation **refuses** (exit 78). The explicit env is **mandatory, not optional**.
- The adjudication is **delta-scoped, not history-scoped**: the migration delta between the previous release tag (`v0.31.0`, the currently
  promoted release) and the new tag is **EMPTY** — this lane adds no `.sql`, and `origin/latest`'s drift adds none (verified:
  `git diff --name-only 0181dadbf origin/latest -- daemon/migrations/versions/` → 0 files).
- The live install's own `v0.31.0` manifest carries `rollback_safe: true` **with** `contains_contract_phase: true` — the identical adjudication
  shape, so this lane follows precedent rather than inventing one.
- **What a rollback to `v0.31.0` means (must be disclosed):** the old release's **defective** `lib.sh`/`promote.sh` come back, so the
  healthy-boot false-halt class **returns** on the next promote until the lane is re-promoted; the daemon-side floor also reverts to
  `1aa0065eb`, i.e. the guard stops demanding the fix again (exactly option D's shape). `rollback_safe: true` means *schema-safe to roll back
  into* — it does **not** mean *defect-free*.
- Recorded in the release `manifest.json`; consumed by promote's T5 manifest gate, `rollback.sh`, and the adopt/sweep paths.
- **Rejected:** leaving it unset (exit 78); adjudicating `0` (unwarranted — nothing schema-bearing ships).

### 3.4 `L-7` — version slot re-derived on **three axes**, never hardcoded

| Axis | Value at record time (verified) |
|---|---|
| `pyproject.toml:3` | `0.31.0` |
| local tags | max `v0.31.0` |
| remote tags (`git ls-remote --tags origin`) | max `v0.31.0` |
| live `releases/` dirs | max `v0.31.0` (`v0.30.9`, `v0.30.11`, `v0.30.12`, `v0.31.0`) |
| **⇒ derived slot** | **`v0.31.1`** — **re-derive at bump time** |

Other lanes are active (a group-chat FE fast-follow commission is open; `prompt_reload` merged to `latest` at `eca98397d` and is **not**
promoted), so a concurrently-claimed slot must be caught **before** the bump commit (`P3-T1`, `AC-P3-1`). The stale project-metadata note
`next_free_version_slot: v0.29.1` is obsolete and must not be trusted.

---

## 4. `L-9` — negative-proof stance and the protection matrix

### 4.1 The doctrine (lane-wide, binding on Phase 2 and on the tester gate)

> **A genuinely wrong release must still halt.** Every accept-path widening ships with its **reddening counter-mutation**. A green assertion
> without a named counter-mutation is not a pin; a mutation that fails to redden its leg is reported as a **failed leg**, never as "no-op".

Two-sided obligation: (a) every wrong-release class still halts; (b) the containment ladder never **over-accepts** — demonstrated by
counter-mutations that **widen the accept-set and must redden** the negative section.

### 4.2 `NP-1…NP-11` → the catching check that must still fire

| NP | Scenario | Catching check (unchanged) | Expected | Reddening counter-mutation |
|---|---|---|---|---|
| **NP-1** | wrong-version green | version conjunct `lib.sh:6107-6116` (`:6113-6116` compare) | rc 1; `version=0.0.0`; `NOT serving the right release`; **exactly one** MainPID poll (holds unqualified — Phase 1 does not touch this block) | `M-NP2` (version bypass ⇒ `if false; then`) |
| **NP-2** | version-absent green | `:6109-6111` | rc 1; "absent/unparseable" | `M-NP2` |
| **NP-3** | non-2xx | `_probe_once_classified` red → probe rc 1 (`:6120-6126`) | rc 1; "hard fail (red …)" | `M-NP2`-adjacent |
| **NP-4** | genuinely-foreign holder (positively `1` on every rung) | ladder ⇒ Layer-1 refusal prefix `:8039-8047` | rc 1; detail **starts with** the live prefix + ladder suffix; **census polled exactly ONCE** (no deferral burn on a positive `1`) | **`M-NP1`** (remove the `1` classification) / **`M-NP1b`** (permissive cgroup test); `M-NP5` reddens the one-census assertion |
| **NP-5** | no-listener green | `:8012-8019` "answered green but lsof finds NO listener" | rc 1 | `M-NP3` (treat rc-0-empty as success) |
| **NP-6** | **prefix-collision containment** | **now a hard pin: segment-aware R2 refuses** (§2) | rc 1 | **`M-NP1b` must redden it** |
| **NP-7a** | halt end-to-end (lib level) | `_supervision_handback_unit` rc 1 (`:6395-6397`) | `restart_via_launcher` rc 1; `NO nohup fallback`; no `hand-back COMPLETE` | `M-B2` |
| **NP-7b** | halt end-to-end (promote level) | caller halt + B4 leave-txn-open (`promote.sh:684-699`, **verified**) | non-zero exit; `halt` row; `in_flight` **still open**; no `commit` row; `current` unchanged | routed to the tester gate (`D-P2-1`) — **declared gap** |
| **NP-8** | late-attributable holder + wrong version | version conjunct runs **after** attribution on every green (`:6107-6126`) | rc 1 + `version=0.0.0`; counter proves the code **did** wait | `M-NP2` |
| **NP-9** | gate-surface parity on the **refusal** | the same shared gate | `gate_livez` **and** `gate_version` both rc 1 with the detail | `M-NP4` (drop the attribution call) |
| **NP-10** | pre-stop MainPID equality | verifier **`:6218-6221`** (verified: WARN + `return 1`; `:6207-6213` is the budget printf) | rc 1 (`EQUALS the pre-stop pid`) | untouched-by-ladder work; no mutation needed (regression obligation) |
| **NP-11** | Darwin variant of `NP-4` | — | **DROPPED** (`D-P2-4`) | declared parity gap |

**Coverage note (P → NP).** Phase-1's §5.7 protection matrix `P1–P12` is the design's promise; the NP set above is where it becomes red-able:
`P1→NP-1`, `P2→NP-2`, `P3→NP-3`, `P4→NP-5`, `P5→NP-4`, `P6→B6(pidless)`/`NP-11-dropped`, `P7→B6`, `P8→NP-4` (`owned:<none>`), `P9→NP-10`,
`P10→B-defer-a`, `P11→NP-3/NP-5 class`, `P12→documented residual` (structural, not substring-related).

### 4.3 Ruled consequences

1. **`NP-6` is now a hard pin** (§2) — arm (b), with `M-NP1b` reddening it.
2. **`NP-7b` is a declared gap**, not a silent omission (`D-P2-1`); `NP-7a` + the §N3 template pin + `B2b` are the compensating evidence.
3. **`NP-11` is dropped and declared** (`D-P2-4`).
4. **Every acceptance added by `L-2` has a counter-mutation**: `M-B1` (rip the ladder out), `M-B1c` (R2 skipped ⇒ `B1c` reddens), `M-B3`
   (invert R2 containment), `M-B4` (drop the MainPID equality arm), `M-B5` (ancestor walk always 0), `M-B6` (empty launchd pid = accept),
   `M-NP1`/`M-NP1b`/`M-NP5`, plus `M-B-defer` for the pooled bound. Each mutation's **restore is sha256-attested** in the evidence pack
   (`phase2-plan.md` §13 item 3).
5. **Non-vacuity is part of the pin**: every negative leg asserts the gate was **reached** (a needle from the gate's own detail **plus** a stub
   call count) and carries a **positive control**; `make_mut` refuses a plant that changed nothing (`cmp -s`).
6. **The command's literal demand is recorded verbatim** in the evidence pack:
   *"NP-4 goes RED under M-NP1 (the `1`-classification removed) and NP-4's 'exactly one census' assertion goes RED under M-NP5 (patience burned
   on a positively-foreign holder)."*

---

## 5. `L-10` — Coalescence closure for board item `wi-d3579d7c` #1

**Item #1 (as framed by the commission):** *promote-kind `flipped=false` live closer — same dead-actor pattern.*

**How this lane closes it without weakening the closer:**

1. **This lane removes the producer.** The incident class produced a `flipped=true` open txn whose only recovery was the daemon-side
   `sweep_serving_green_closer` (`upgrade_journal_sweep.py:2325`, P1–P9 `:2335-2350`). A healthy boot no longer halts ⇒ no open txn is created
   ⇒ no closer rescue is needed. (This run class has now fired **three** times: v0.29.0, v0.29.1, v0.31.0.)
2. **The closer's predicates are untouched.** P5 (`flipped == true`) stays; P9 (`intent_flip` authorization) stays; the P6/P8 windows stay. A
   `flipped=false` dead-actor txn is still **not** closable — by design: there is no flip to justify a commit, and widening P5 would let an
   unflipped arm be "committed" on health alone, which is the authorization hole P9 exists to close.
3. **`daemon/services/upgrade_journal_sweep.py` is unchanged** (S10 / `I-E1`), and **no budget knob changes** ⇒ the grace coupling
   `max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)` remains satisfied as written, and the closer keeps its full 300 s / boot-grace
   behaviour on genuinely unhealthy boots.
4. **Residual, stated for the board:** a `flipped=false` stuck txn from **other** causes (e.g. an arm whose executor died pre-flip) remains the
   boot sweep's / `restart_abandoned` lane's problem — unchanged, out of scope here.

**Zero daemon-side changes beyond the constant re-pin.** The only `daemon/**` edit in the whole lane is
`upgrade_tools.py:4145 PIPELINE_SCRIPTS_FLOOR_COMMIT` (a value), which carries no runtime behaviour.
**Bookkeeping note:** board item text is not file-accessible from this lane (the PM/board store is the daemon DB); the closure above is the
technical rationale the board owner applies.

---

## 6. `L-11` — Deferred register

| # | Item | Status | Why it is acceptable now | Compensating coverage | Owner |
|---|---|---|---|---|---|
| 1 | **T12** — Darwin `_flip_verify_portattribution` widening (`lib.sh:6996`) | **DEFERRED** to a named follow-up lane | incident is Linux/unit-anchored; R3/R4 already give mode-general accept paths; widening without a provable Darwin stub leg exceeds the ceremony-critical minimum | `B7` arm selection; `B6` launchd rung; Linux fix is the certified one | follow-up lane |
| 2 | **NP-11** — Darwin variant of `NP-4` | **DROPPED** (declared parity gap) | mechanical consequence of #1 | `NP-9` parity on **this** host; `B6`/`B7` | declared in the evidence pack |
| 3 | **Non-journal restart-audit channel** for the 5 pre-`journal_init` exits (`:82`, `:96`, `:104`, `:108`, `:145`) | **DEFERRED** (joins #1 in the deferred register) | a journal write is structurally impossible there; visibility exists on stderr + client log | `R0` pins the **6-entry allowlist**; `R-PY` two-sided leg | follow-up lane |
| 4 | **Tier-3 promote-level rehearsal** (`NP-7b`, `B2b` caller contract) | **NOT deferred — OWNED ELSEWHERE**: routed to the **tester gate** (`D-P2-1`) | route decision, not a gap-waiver | lib-level `NP-7a`; `§N3` template pin; `§11.4` hand-off spec | tester gate (coding commission cycle) |
| 5 | `make_systemctl_stub` 3-copy consolidation | **ROUTED FINDING** (`RG-6`), not this lane | consolidation would churn 3 standing suites | the `§4.3` knob contract is the consolidation spec; the new stub is a superset | future consolidation |
| 6 | `test_release_journal.sh` 600 s cap (RC=124 at this tip) | **FILED** (`RG-6`-adjacent / §12.3) | pre-existing, disclosed in PACKS | aggregator keeps it `--full`-only with its disclosure | existing lane |

**Not in this register** (explicitly out of scope, so they are not mistaken for deferrals): the arm's `pending_op` persistence, the
`lock_acquire` `to_thread` move, the boot-sweep default (board sub-items 2–4); the ambient-`POSTGRES_*`/auth-scrub debt in standing suites;
`test_adaptive_soak.sh`'s divergent C-10a arithmetic (recorded as a routed finding only).

---

## 7. `L-12` — Corrections register (so stale claims are never re-imported)

### 7.1 The five commission-level corrections (ruled)

**(a) "The three suites are NOT registered in `PACKS.md`" — WRONG.** All three **have rows** (verified by grep in this worktree):

| Suite | Rows (verified) |
|---|---|
| `test_release_journal.sh` | `PACKS.md:1826`, `:1827`, `:1828` (CURRENT tip state), `:2514`, `:3021`, `:3123` |
| `test_stage_freshness_guard.sh` | `:1829` (`shell_stage_freshness`, 110P/8F ≡ base) and `:4618` (**"registered 2026-10-05 — direct suite"**, 118/0) |
| `handback_verify_budget_test.sh` | `:1364` (L8b flake disclosure), `:4813`, `:4854` (two registration rows) |

The F-2 class from the 2026-10-05 RESULTS file is **closed**; what remains is registry *shape*. **The real gaps are two other things**, and they
are what Phase 2 plans:

- **G-1 — no canonical per-lane section**: today's rows are historical commission blocks scattered across ~5k lines; a reader cannot find the
  lane's current invocation + observed count in one place ⇒ **`RG-3`**.
- **G-2 — ZERO aggregator wiring**: `tests/test_upgrade_overhaul_all.sh` runs `release_journal` only under `--full` (`:184-196`, verified) and
  never runs `test_stage_freshness_guard.sh` **or** `handback_verify_budget_test.sh` (verified: `grep` → zero hits) ⇒ **`RG-4`/`RG-5`**.

**Also superseded:** `research-findings-code.md` NOT-FOUND item 6 ("Registration … NOT-FOUND") is a *commit-time-scoped* observation; it must
**not** be re-imported as current truth. `RG-1`/`RG-2` (new pack rows) remain required — for the **new** packs, not for the old ones.

**(b) No existing `systemctl` stub can emit a nonzero `is-active` rc — new stub generation is a BLOCKING prerequisite.** All three stub sites
answer `is-active` with `exit 0` (`tests/packs/handback_verify_budget_test.sh:218`, `tests/test_supervision_stop_handback.sh:120`,
`tests/test_bounded_subprocess_waits.sh:100`). **No existing fixture can produce rc 3 — the defect's core input.** Therefore `P2-T1` (the stub
generation with the `§4.3` knob contract: `SC_IS_ACTIVE_RC`, `SC_IS_ACTIVE_ERR`, `SC_RESET_FAILED_RC/_ERR`, `SC_SHOW_RC/_ERR`, argv `SC_LOG`)
is a **prerequisite for every `A`-leg**, not an optional convenience. Nothing in the `A`/`C` leg families can start before it lands.

**(c) The C-10a two-copy table identity is asserted by NO pack.** Verified: `grep -rln "BOUND TABLE" tests/` → **zero files**;
`tests/test_ceremony_wallclock.sh` binds the table to `lib.sh` only, while `promote.sh:326-327` *claims* byte-identity. **The blocks are in fact
byte-identical today — for exactly 17 lines** (`lib.sh:157-173` == `promote.sh:309-325`, verified by `diff`: no output). Because Phase-1 `T9`
edits **both** copies, the sync pin is the proof the "same edit in two places" happened ⇒ **leg `B10`** (mutation `M-B10`: one word into the
`promote.sh` copy ⇒ red).

**(d) Phase-1 anchor drifts flagged by Phase 2 — status now closed by re-measurement.**

| Claim | Re-measured truth |
|---|---|
| Phase-1 `C-5` cites `:6207-6213` for the pre-stop verdict | **`:6207-6213` is the budget printf** (comment + `printf`); the pre-stop-equal `_warn` + `return 1` are at **`:6218-6221`** ⇒ `NP-10` must anchor on `:6218-6221` (as Phase 2 wrote) |
| `promote.sh:1051-1056` "unverified" | **VERIFIED EXACT**: `:1051` `if ! restart_via_launcher; then` … `:1055` `exit 1`, `:1056` `fi`; halt journal at `:1053-1054`. T11's range is precise. |
| Phase-1 `A23` cites `tests/test_bounded_subprocess_waits.sh:376` for the happy-path 7b pin | the pin is at **`:377`** (`assert_not_contains … "systemctl start"`); cosmetic, but the leg should anchor on `:377` |

**(e) Surgery-era anchor drift — already corrected in the research files; restated here so no one re-imports the leads.**

| Stale lead | Actual |
|---|---|
| attribution gate at `:6921-6955` | `_gate_serving_attribution` is **`lib.sh:8004-8092`**; `:6921-6950` is **`_supervision_owned_pids`** |
| `scope_arm_is_active` as a function | **not a function** — inline code in `restart_via_launcher` **`lib.sh:6695-6703`**, with `site=scope_arm_is_active` as a **journal label** at `:6700` |
| "frozen `SUPERVISION_*` exports in `promote.sh:676-680`" | **no frozen/exported vars** — `:673-683` is a **comment block**; the real contracts are the test-pinned **7b** substring trap and **7c** frozen wording |
| `sweep.py:405` | the sweep lives in **`daemon/services/upgrade_journal_sweep.py`**; no `sweep.py` exists in the tree |

### 7.2 Additional corrections found by this pass (new; not in the ruling set)

**(f) Env-var spelling — two different typos in `phase3-plan.md`; the real name is `ENSEMBLE_UPGRADE_SCRIPTS_DIR`.**
Verified: `upgrade_tools.py:2050` `os.environ.get("ENSEMBLE_UPGRADE_SCRIPTS_DIR", …)`; live `.env:107` carries that exact key.
`phase3-plan.md` F12 writes `ENSEMBLE_UPGRIPE_SCRIPTS_DIR` and `§4.4` writes `ENSEMBLE_UPGRIPADE_SCRIPTS_DIR`. Operators copy these lines into
tag annotations and handoff reports ⇒ **use the verified spelling** in every artifact this lane emits.

**(g) `A22`'s C-10a range is loose.** `phase1-plan.md` `A22` cites `lib.sh:157-181` / `promote.sh:309-327` as the duplicated table. The
**identical** span is **17 lines: `lib.sh:157-173` / `promote.sh:309-325`** (verified `diff` empty); `lib.sh:174` is the knob line and
`promote.sh:326-327` is the byte-identity *claim*. `B10`'s **anchor-comment → "downstream boundary with the daemon up"** extraction is correct
(the end anchor lands on the right line); only the numeric range overstates. Do not `cmp` the whole loose range — it will not match.

**(h) The restart exit-78 census needs exact line discipline.** `scripts/upgrade/restart.sh` contains **15 code `exit 78` statements**
(`:82, :97, :105, :108, :124, :137, :145, :149, :156, :162, :166, :174, :182, :188, :192`); `grep -c "exit 78"` returns **17** because
`:52` and `:118` are **comment** mentions. Consequences:
- `phase2-plan.md` §2 scope item 5 says "every `exit 78` site (**18**)" — inconsistent with its own §10.1 table (15 restart.sh rows + 2
  external rows: `lib.sh:1494` live-restart-refused and `restart.sh:213` `supervision_preflight`'s own `_refuse` = **17 classified rows**).
- `phase2-plan.md` §10.1 says "the table below uses the **exit** lines" but four rows carry **_warn**/one-off line numbers
  (`:96`→exit `:97`, `:104`→exit `:105`, `:122`→exit `:124`, `:136`→exit `:137`).
- **Binding for `R0`:** anchor on literal `exit 78` statements, self-check `grep -c` (17 raw hits ⇒ 15 code + 2 comments), and classify every
  code site. The **allowlist stays 6** (`:82`, `:96`, `:104`, `:108`, `:145`, `:192` by `_warn`/statement line).

**(i) Phase-3's own ID convention vs its content.** The header declares `P3-D#` / `P3-R#` / **`P3-T#`** / **`AC-P3-#`**, but the document
uses **unprefixed `T1–T10`** and **`AC1–AC13`** — which **collide** with Phase-1's `T1–T12`. Its "no numeric collision" note is therefore only
true for `D`/`R`. Also its note says "Phase 1 owns … `T1–T8`", but Phase 1 has **`T1–T12`** (T9 = C-10a sync, T10 = restart rows,
T11/T12 = optional). **Disposition:** cited here as `P3-T*` / `AC-P3-*`; phase file unedited; the commission must not treat the note's
"T1–T8" as the Phase-1 task range.

**(j) `plan-overview.md` does not exist yet.** The lane directory holds exactly five files (both research files + three phase plans).
Two decisions assign it duties — `D-P3-3` ("`plan-overview.md` mirrors the same set") and `D-P3-1` ("plan-overview.md carries it to the
caller"). Those duties are **planner actions** (Appendix B), not satisfied facts.

**(k) Minor wording hazard in Phase 1 §4.3's example log line.** `_log "is-active: ${isact:-inactive|activating} (rc=3) …"` would literally
render the token `inactive|activating` (a `${var:-word}` expansion is not a choice). Phase 1's own note requires sanitizing the state token to
`[a-z-]{1,20}` — implement the sanitized token, do not paste the example verbatim.

---

## 8. Consistency pass — the lane's cross-plan reconciliation

### 8.1 Method

1. **ID namespace census** for all three phase files (every prefix, every range).
2. **Cross-reference walk**: every `§`-reference, every invariant/leg/mutation ID, every `file:line` cited by one phase about another phase's
   surface.
3. **Anchor re-verification** in this worktree (Appendix A): 20 independent greps/reads, including every anchor this register itself cites.
4. **Body-vs-ledger diff** for the one amended predicate (§2.4) and for the ruled-answer status of every `§15`/`§17`/`§10` open decision.

### 8.2 Namespace map (what exists, so citations stay unambiguous)

| File | Namespaces in use |
|---|---|
| `phase1-plan.md` | `A1–A28` (anchor register), `T1–T12` (tasks), `C0–C6` (commits), `C-1…C-6` (stash touchpoints), `R1–R4` (containment **rungs**), `R1–R5` (§12 **risks**), `P1–P12` (protection matrix), `I-A1…I-E3` (invariants, **29**), `N1–N9` (negative-proof template), `S1–S10` (success), `D-P1-1…3` |
| `phase2-plan.md` | `D1–D7` (incidents), `A0–A7` (Candidate-A legs), `B1–B10` + `B-*` (Candidate-B legs), `C-1…C-8` (Candidate-C legs), `NP-1…NP-11`, `M-*` (mutations), `R-*` (restart legs), `R0`, `RG-1…RG-7`, `P2-T1…P2-T16`, `U-1…U-4`, `OQ-P2-1…4`, `R-1…R-5` (risks), `SC-1…SC-11` |
| `phase3-plan.md` | `F1–F36` (facts), `P3-D1…P3-D12`, `P3-R1…P3-R7`, `T1…T10` (**should be `P3-T*`**), `AC1…AC13` (**should be `AC-P3-*`**), `P3-Q1…P3-Q3` |

### 8.3 Findings (listed explicitly; **not** silently fixed in the phase files)

| ID | Finding | Severity | Disposition |
|---|---|---|---|
| **CP-1** | `phase1-plan.md` uses **`R1–R4`** for the containment **rungs** (§5.3) **and** **`R1–R5`** for the phase **risks** (§12) — same letter+digit space inside one file. | doc-hygiene (high confusion risk) | **No content conflict.** Binding citation rule: "rung R2" / "§12 R3". The amendment (§2) is phrased against "R2 the rung". |
| **CP-2** | Three near-identical label families for different objects: **commits** `C0–C6`, **stash touchpoints** `C-1…C-6`, **Phase-2 legs** `C-1…C-8`. | doc-hygiene | Cite as "commit `C4`" / "touchpoint `C-4`" / "leg `C-4`". Leg names deliberately mirror their touchpoints (1:1 mapping is a feature), but `C4`≠`C-4`. |
| **CP-3** | Phase 3 declares `P3-T#` / `AC-P3-#` but uses unprefixed `T1–T10` / `AC1–AC13`, colliding with Phase-1's `T1–T12`. | namespace collision (real) | Correction **(i)**; this register relabels at citation (`P3-T*`, `AC-P3-*`); phase file unedited. |
| **CP-4** | Phase 3's note says Phase 1 owns "`T1–T8`"; Phase 1 actually has **`T1–T12`** (T9/T10 landed in the plan; T11/T12 optional). | stale cross-reference | Correction **(i)**. |
| **CP-5** | Phase-1 `A22` block range (`157-181`/`309-327`) overstates the byte-identical span (17 lines: `157-173`/`309-325`). | anchor range drift | Correction **(g)**; `B10`'s anchor-based extraction is correct as specified. |
| **CP-6** | Phase-2 scope item 5 says "`exit 78` site (**18**)"; its own §10.1 table yields **15** restart.sh code sites (+2 external rows = 17). | arithmetic drift | Correction **(h)**; `R0` must self-check the literal `grep -c`. |
| **CP-7** | Phase-2 §10.1 claims the table uses **exit** lines, but 4 rows carry `_warn`/one-off lines (`:96→97`, `:104→105`, `:122→124`, `:136→137`). | internal inconsistency | Correction **(h)**; `R0` anchors on statements, not table numbers. |
| **CP-8** | Phase-1 `C-5` cites `:6207-6213` for the pre-stop verdict; actual `:6218-6221` (`:6207-6213` = budget printf). | anchor drift | **Already flagged by Phase 2 (`NP-10`); confirmed by re-measurement**; correction **(d)**. |
| **CP-9** | Phase-1 `A23` cites the happy-path 7b pin at `:376`; actual `:377`. | cosmetic | Correction **(d)**; leg anchors on `:377`. |
| **CP-10** | Phase-1 §4.3's example `_log` string would render a literal `inactive|activating` token. | implementation hazard | Correction **(k)**; implement the sanitized token Phase 1 itself requires. |
| **CP-11** | Invariant count: the dispatch brief says "**25** invariants `I-A1…I-E3`"; the tree has **29** (`A1–A5`=5, `B1–B12`=12, `C1–C5`=5, `D1–D4`=4, `E1–E3`=3). Phase 2's mapping table covers **all 29**. | count drift (brief) | **29 is the count of record.** No invariant is unmapped; nothing needs adding. |
| **CP-12** | Phase-3 header says "Phase 2's §9 negative proof (`NP-1…NP-8`)"; Phase 2's NP set is `NP-1…NP-11`. | stale cross-reference | Cosmetic; the union `NP-1…NP-11` governs (`NP-9`/`NP-10`/`NP-11` are Phase-2's own additions after Phase 3 was drafted). |
| **CP-13** | Phase-3 F12/§4.4 env-name typos (`ENSEMBLE_UPGRIPE_…` / `ENSEMBLE_UPGRIPADE_…`). | **operational** (copy hazard) | Correction **(f)**. |
| **CP-14** | `plan-overview.md` absent while two decisions assign it duties. | pending artifact | Correction **(j)**; planner action (Appendix B). |

### 8.4 Cross-plan contradictions — verdict

**No design contradictions found.** What looked like contradictions are **open decisions now ruled**, each resolved exactly once:

| Apparent tension | Resolution |
|---|---|
| Phase 1 §15 asks rc=3 policy; Phase 2 `U-1` asserts under **both** policies | `D-P1-1` → no row; `A3`'s policy-pinned assertion is the one that bites (`M-A3b`) |
| Phase 1 §15 asks deferral exposure; Phase 2 `U-2` resolves either way | `D-P1-2` → test seam 5 s pooled; `A7(iii)` asserts the exact shape |
| Phase 1 §15.3 / Phase 2 `U-3` leave T11/T12 open | `D-P1-3` → T11 in, T12 deferred; `C-7` gains the rollback row; `NP-11` dropped (`D-P2-4`) |
| Phase 2 `NP-6` two-armed | `D-P2-3` → arm (b) is the contract (**amendment**, §2) |
| Phase 2 `OQ-P2-1` Tier-3 ownership | `D-P2-1` → routed to tester gate; `NP-7b` declared gap |
| Phase 2 `OQ-P2-2` 5 silent exits | `D-P2-2` → acceptable, `R0` pins the allowlist |
| Phase 3 `P3-Q2` scripts-pin owner | `D-P3-2` → operator-owned precondition |
| Phase 3 `P3-Q3` CHANGELOG set | `D-P3-3` → five items confirmed |
| Phase 3 `P3-Q1` rebase-vs-merge | `D-P3-1` → **escalated to the leader** (§9) |

**Dangling references:** none found. Every `§`-cross-reference inside the set resolves (Phase 1 §4/§5/§6/§7/§8/§10–§15; Phase 2
§4.2/§4.3/§5.2/§5.3/§7.5/§9/§10.1/§11.2/§11.4/§12/§13/§17; Phase 3 §4.1–§4.5/§5/§6/§7/§8/§10); every invariant `I-*` has a leg row; every
`T`/`P3-T` task is reachable from an AC; the commit graph (`C1…C6` → `F`) matches Phase 3's `F→M→B→T`.

### 8.5 The one cross-plan seam that must not be lost

**`phase3-plan.md` `P3-R4`** (also `phase2-plan.md` §2 out-of-scope row 2): any **new shipped file** under `scripts/upgrade/` or
`daemon/plugin_subsystem/` must be added to `executor_family_paths` **inside the lane branch** (so `M` contains both the member and the list
edit). **Current state: satisfied by construction** — Phase 1 adds no files (edits `lib.sh`, `promote.sh`, `restart.sh` only), Phase 2 adds
only `tests/**` + `PACKS.md` rows. **17 family paths verified.** If implementation departs from this, the coverage gate fails at stage time
(exit 78) and `P3-D2`'s fallback (a split `fix(floor):` commit) applies.

---

## 9. Open item escalated to the leader — `D-P3-1` (rebase vs merge)

**Recorded as the lane's SINGLE escalated decision.** The planner escalates it in the final report; `plan-overview.md` must carry it to the
caller.

**The question.** `origin/latest` is **`eca98397d`, 55 commits past the lane base `0181dadbf`** (verified). The drift touches `daemon/` and
`.agents/tester/PACKS.md` (Phase 1/2's surface) but **none** of `scripts/upgrade/`, `daemon/tools/upgrade_tools.py`,
`daemon/migrations/`, or `frontend/` (Phase-3 `F31`). Local `latest` is **232 commits behind** and is checked out in
`/home/nea/ensemble-src-wt-ari-stop-int` (`git worktree list` verified), so git refuses a second `latest` checkout. **Rebase or merge the lane
branch onto `origin/latest`?**

**Why this register does not decide it.** House FINALIZE convention makes rebase-vs-merge the **leader's** call (and the house rule is explicit:
fetch, verify `latest` unmoved from the branch base; **moved ⇒ STOP and report** with new sha / subject / distance / overlap).

**Why it does not block the rest of the record.** The mechanics are **agnostic**: `F → M → B → T` holds under either disposition; only
**`M`'s identity — and therefore the stamp/floor value — changes**. Everything Phase 1/2 specifies (code, tests, legs, mutations) is
disposition-independent. Phase-3 `P3-T2`/`P3-T3` are blocked on the call; nothing else is.

**What the leader is choosing between (and what follows from each):**

| Answer | Consequences |
|---|---|
| **Merge** (`--no-ff` of `origin/latest` into the lane branch, then the lane merges to `latest`) | extra merge commit(s); `M` is the landing merge; conflicts resolved in the lane branch (PACKS.md ⇒ keep-both newest-first) |
| **Rebase** onto `origin/latest` | linear history; the lane's `F` commits are re-written (sha churn); `M` is unchanged in *function* (the landing merge) but every pre-merge sha moves; the Phase-2 evidence pack's recorded shas must be re-stamped if rebased **after** the test run |

**Recommended framing (not a ruling):** whichever is chosen, cut the tag **last** (`P3-T5`), after `B` is on `origin/latest`, so a moved tip
invalidates the tag **before** any build spend.

---

## Appendix A — Anchor re-verification log (this pass)

All read-only in `/home/nea/ensemble-src-wt-hbfix` at record time.

| Anchor | Verified value |
|---|---|
| `lib.sh:95` | `PIPELINE_SCRIPTS_REV=1aa0065eb818ff00cb32730fab54d7f2060c42d8` |
| `upgrade_tools.py:4145` | `PIPELINE_SCRIPTS_FLOOR_COMMIT = "1aa0065eb818ff00cb32730fab54d7f2060c42d8"` |
| `lib.sh:4856` / `:6921` / `:6996` / `:7037` / `:7092` / `:7999` / `:8004` / `:8100` | function starts: `_run_bounded`, `_supervision_owned_pids`, `_flip_verify_port_attribution`, `_supervision_port_holders`, `_supervision_pid_in_launchd_lineage`, `_gate_attr_unprobeable`, `_gate_serving_attribution`, `_gate_attr_unit_lineage` |
| `lib.sh:6332-6339` | reset-failed: `_run_bounded` then unconditional `_warn "…exceeded budget…"` + `kind=budget … rc=124` (**hard-coded**) |
| `lib.sh:6354-6360` | `isact_rc` capture; `[ "$isact_rc" -ne 0 ]` ⇒ "exceeded budget" + `kind=budget … rc=$isact_rc` (incident shape) |
| `lib.sh:6695-6703` | scope arm: identical conflation; `site=scope_arm_is_active` at `:6700`; `_sc_isact=""` at `:6703` |
| `lib.sh:6093-6106` | probe: green ⇒ `_gate_serving_attribution`; refusal ⇒ `_warn` + `return 1` (fail-fast) |
| `lib.sh:6207-6213` | budget **printf** (comment + `printf … >&2`) — **not** the pre-stop verdict |
| `lib.sh:6218-6221` | pre-stop-equal `_warn` + `return 1` |
| `lib.sh:157-173` == `promote.sh:309-325` | **byte-identical, 17 lines** (`diff` empty); `promote.sh:326-327` = the identity claim |
| `lib.sh:494-497` | `_COOLDOWN_DATE_UNAME` observable-override precedent |
| `_GATE_ATTR_DEFER_S` | **absent** from `scripts/` (new seam, as designed) |
| `lib.sh:6748`, `:6753` | frozen 7c wording `falling back to nohup launcher (comp7 opt-in path)` |
| 7b pins | `test_supervision_stop_handback.sh:288/768/840/862`; `test_bounded_subprocess_waits.sh:377`; `handback_verify_budget_test.sh:522/534/603` |
| `test_bounded_subprocess_waits.sh:429` | `assert_contains … "is-active probe exceeded budget"` (the legacy byte-identity pin) |
| `promote.sh:684-699` | hand-back halt: `HB_ERR` read, rm-after-read, `journal_history_append halt …`, `exit 1` |
| `promote.sh:1051-1056` | rollback hand-back halt block (`if ! restart_via_launcher … fi`), journal `:1053-1054`, `exit 1` `:1055` |
| `restart.sh` exit 78 | **15 code statements**: `82, 97, 105, 108, 124, 137, 145, 149, 156, 162, 166, 174, 182, 188, 192` (+2 comment mentions `52`, `118`) |
| `restart.sh:141-174` | `_log preflight`; `journal_init` fail ⇒ `:145`; TORN ⇒ `:149`; no `pending_op` ⇒ `:156`; pop-kind ⇒ `:162`; pop-run ⇒ `:166`; inflight-kind ⇒ `:174` |
| `lib.sh:738` / `:773`; `executor_family.sh:315` | `_pipeline_scripts_stamp_verdict`, `_pipeline_scripts_floor_verdict`, `executor_family_verify_stamp_coverage` all exist |
| `bash scripts/upgrade/executor_family.sh --paths` | **17** paths |
| `PACKS.md` | rows verified at `1826-1828`, `1829`, `1364`, `4618`, `4813`, `4854`, `2514`, `3021`, `3123` |
| `test_upgrade_overhaul_all.sh` | `release_journal` only under `--full` (`:184-196`); **zero** hits for `stage_freshness` / `handback_verify_budget` |
| `grep -rln "BOUND TABLE" tests/` | **zero files** |
| `upgrade_tools.py:2050` | `os.environ.get("ENSEMBLE_UPGRADE_SCRIPTS_DIR", …)` (correct spelling) |
| version slot | `pyproject` `0.31.0`; local tag max `v0.31.0`; remote tag max `v0.31.0`; live `releases/` max `v0.31.0` ⇒ **`v0.31.1`** |
| `stage.sh` | `--version <v>` accepted (`:142-145`); target token `live` required (`:141`) |
| tip drift | `origin/latest` = `eca98397d`; `rev-list --count 0181dadbf..origin/latest` = **55**; local `latest` **232** behind, held by `wt-ari-stop-int` |

---

## Appendix B — Planner actions outstanding (not satisfied by this file)

1. **Author `plan-overview.md`** with: the five-item disclosure set (`D-P3-3`); the **escalated `D-P3-1`** carried explicitly (`§9`); the
   phase table (`phase1/2/3-plan.md`); the amendment pointer (§2); the deferred register (§6); the **29** invariants (CP-11).
2. **Mirror the correction register** (§7) into the overview's assumptions/notes so a future reader of the overview alone does not re-import
   the "(a) not registered" claim or the env-name typos.
3. **Record `D-P3-1` as open** in the overview's open-questions section until the leader rules.
4. **Do not renumber** anything when consolidating (`L-*` are the only minted IDs; phase namespaces are preserved).

---

## Appendix C — Ratification trace (dispatch ruling → decision → binding section)

| Dispatch ruling | Decision | Bound section(s) |
|---|---|---|
| rc=3 policy (phase1 §15) | `D-P1-1` | §1.1, `phase1-plan.md` §4.1/§4.3; `phase2-plan.md` §6.1 |
| deferral exposure (phase1 §15) | `D-P1-2` | §1.1, `phase1-plan.md` §5.4; `phase2-plan.md` §7.5 |
| optional items T11/T12 (phase1 §15) | `D-P1-3` | §1.1, §6, `phase1-plan.md` §6/§10 |
| A/B/C + restart-arm adoptions | `L-1`…`L-4` | §1.2 |
| Tier-3 rehearsal (phase2 §17) | `D-P2-1` | §1.3, §4.3, §6 |
| 5 pre-journal-init exits (phase2 §17) | `D-P2-2` | §1.3, §6 |
| NP-6 prefix collision (phase2 §17) | `D-P2-3` | **§2 (amendment)**, §4.2 |
| Darwin NP-11 (phase2 §17) | `D-P2-4` | §1.3, §4.2, §6 |
| rebase vs merge (phase3 §10) | `D-P3-1` | **§9 (escalated)** |
| scripts-pin owner (phase3 §10) | `D-P3-2` | §1.4 |
| CHANGELOG disclosure set (phase3 §10) | `D-P3-3` | §1.4, Appendix B |
| floor graph (phase3 §4.2/§5) | `L-5` | **§3.1–§3.2** |
| `rollback_safe` (phase3 §4.5) | `L-6` | **§3.3** |
| version slot (phase3 §5) | `L-7` | **§3.4** |
| no-promote boundary (phase3 §1/§5) | `L-8` | §1.5 |
| negative-proof doctrine | `L-9` | **§4** |
| coalescence closure (wi-d3579d7c #1) | `L-10` | **§5** |
| deferred register | `L-11` | **§6** |
| corrections register | `L-12` | **§7** |

**No decision in this register re-opens a dispatcher ruling.** Where a ruling narrowed a plan (`T12`, `NP-11`, rc=3 journaling), the narrowing
is explicit, its compensating coverage is named, and the affected phase-plan section is cited so the coding commission works from the ledger
of record — this file — where the phase body and the ledger disagree.
