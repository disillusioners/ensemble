# Architecture Recommendation R2 — unified transient-resolution redesign (approver iter-002 blockers)

**Commission:** promote-handback-false-halt, round 2 — approver iteration 002 REJECTED (B1/B2/B3); leader architectural ruling received; iteration 003 is FINAL before escalation.
**Date:** 2026-11-18 (UTC) · **Author:** architect (controller) — 2-worker fan-out, architect-synthesized and -adjudicated.
**Workers:** `a93946e3` ground-truth verification (skill `adversarial-plan-ledger-verification`) · `4ad3860d` unified design (skill-less, structural method).
**Inputs:** approver tracking `/home/nea/ensemble-src/.agents/approver/promote-handback-false-halt-tracking.md` (iter 001+002) · plan set @ `4a3c26e28` (PoR `d7ff393e7` + repairs `0fab8fb88`/`933ca61d2`/`4a3c26e28`) · code @ `/home/nea/ensemble-src-wt-hbfix`. All anchors re-grepped at `4a3c26e28`; `git diff --stat 0181dadbf..HEAD` touches only the plan dir ⇒ every code fact is base-tree truth.

## 0. Corrected premise (on the record — supersedes r1's carry)

Round-1's recommendation carried the plan's then-claim that the deferral's terminal refuses flow into "the existing halt chain + B4 leave-txn-open". **That premise is false at the carrier-set surfaces:** B4 leave-txn-open exists ONLY at hand-back (`promote.sh:684-703`, `restart.sh:251-263`, `rollback.sh:249-263`), which is UPSTREAM of every post-flip gate consumer (full leave-open census: 12 sites, none inside a gate-consumer arm). The carrier must carry its own terminal arms. The approver is right; this document designs to that.

---

## 1. Ground truth (worker D, code-verified at `4a3c26e28`)

**Blocker verdicts:** B1 **CONFIRMED all five sub-claims** — sharpened: under `set -u` (`promote.sh:69`) a literal T7d is EITHER an unbound-variable crash at `:873` OR a fall-through into 8b auto-rollback + `journal_quarantine` (`:1091`) of a possibly-healthy release with an EMPTY reason — never the specified halt. B2 **PARTIAL** — the sweep code is at `:2402-2404` (P4) / `:2442-2452` (P9), not the cited `:2335-2350` (that is the docstring); **P4 excludes `kind=restart` ONLY**; **P9 is per-TARGET, not per-txn** (stated `:2437-2439`) ⇒ rollback-kind to a previously-promoted target IS closer-reachable — but converges to **wrong semantics** (D3). B3 **CONFIRMED in outcome, imprecise in mechanism** — the first-order failure is D1 below, which is *worse* than the approver's cap-reach mechanism.

**Four defects the approver missed (all now in scope of this design):**
- **D1 — the soak tick site is UNWIRED (4th unwired site).** `lib.sh:8382-8386` (adaptive) and `:8294` (conservative) collapse ANY `gate_version` failure into `return 1` with the label **"soak FAILURE: version drifted"**. T7c's site list and C3's scope omit both lines ⇒ literal T7c forges a false drift label and an instant red at tick 1.
- **D2 — `restart.sh:326` is a 4th one-shot consumer** (version identity, `:321-338`) outside T7c's `:284-300` range; a transient there lands as closed-txn + false `version verify mismatch (expected V)` label.
- **D3 — the daemon closer can COMMIT-CLOSE a stale rollback-kind txn** (P4 passes rollback; P9 per-target passes) writing `previous=displaced_version` = the failed version the rollback was escaping — precisely the `previous==quarantined` M4-strand shape `promote.sh:1077-1083` guards against. ⇒ The rollback surface must NEVER leave its txn open.
- **D4 — the promote cure needs a NEW leave-open halt site:** every existing promote halt arm closes the txn (`:979/:990/:1000/:1011/:1097/:1106/:1114`); only the hand-back arm `:698` does not. `halt` is an existing event kind (I-E3 holds) — but "no new halt machinery" (`phase1 :689-690`) does NOT; **drop that assertion**.
- **D5 — `adopt-unit.sh:453`** verifies `/livez` with raw `curl -fsS`, no attribution — post-lane it is the only verify surface that trusts any answerer. Register out-of-scope (pre-existing posture; not created by this lane) — see §8.
- **D6 — restart closers are `pending_op`-conditional:** `close_stale_restart_arm` (`upgrade_journal.py:2800`, boot) and `close_restart_arm_executor_exit` (`:2909`, reaper) silently no-op without a matching `pending_op`. ⇒ Invariant: any restart disposition that leaves state behind must retain `pending_op`; the design avoids the class by closing at the surface.

**Recordable-outcome inventory (what "closed cleanly, NOT success" can be built from):** no single journal primitive exists for it — the composition is `halt` (existing terminal event, existing `halted-for-human` label + `ledger_check.py:74` mapping) or `restart_gate` (existing `RESTART_TERMINAL_EVENTS` member) **plus an explicit `journal_close_txn`**. `halt` alone does NOT close `in_flight`. No new event kind is needed ⇒ I-E3/A7 hold.

**Anchor corrections (corpus-wide, must land):** 8a test is `promote.sh:873` (not `:874` — 7 stale occurrences); sweep code `:2402-2404`/`:2442-2452`; soak code `lib.sh:8487-8498`; symbol is `_flip_verify_port_attribution`; §5.4b's restart range must extend to `:321-338`.

---

## 2. The design (worker E, adjudicated; leader ruling instantiated)

### 2.1 Mechanism — one producer, one boundary
`rc=2` is minted at exactly ONE site: the exhaustion arm inside `_gate_serving_attribution` (`lib.sh:8004-8118`) — meaning *"deferral budget expired, still unattributable"*. The deferral loop runs to pool exhaustion INSIDE the gate, so `2` crosses the boundary exactly once. Every intermediate collapse is removed (`lib.sh:8159`).
- `0` = definitive green (attributed ∧ version-conjunct satisfied) · `1` = definitive failure (answered-wrong / positively-foreign / hard red / no green at all in probe budget) · `2` = inconclusive.
- **Deliberate asymmetry (recorded as a decision, not an accident):** `_probe`'s budget-exhaustion `2` ("nobody answered") is NORMALIZED to `1` at livez/readyz — *no answer at all* is a health verdict; *green-but-unattributable* is not. Only the second gets the never-rollback contract. Blast radius stays on the attribution class this lane exists for; every existing rc=1 pin stays green.
- **Carrier:** ONE canonical name `gate_defer_reason` in all three scripts (the three aliases `GATE_DEFER`/`REGATE_DEFER` are STRUCK — three names are three omission chances, the defect class itself). Read/written **exclusively** inside the helper family via `${gate_defer_reason:-}` forms ⇒ `set -u`-safe by construction (closes approver note 2 and D's B1a crash sharpening with zero per-script initializers).

### 2.2 Uniform consumer contract — one write site, one read site
Two symbols in `lib.sh`; no other reader/writer of any carrier anywhere:
- **`_gate_consume <surface> <rc> <label>`** — called as a STATEMENT immediately after each one-shot gate call (never inside `$( )` — its write is a shell assignment; census pin enforces zero `$( _gate_consume`).
  - `rc=0` → no-op. · `rc=1` → sets the surface's failure carrier (`gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL`) with today's byte-exact string; return 1.
  - `rc=2` → sets `gate_defer_reason` with transient wording; failure carrier UNTOUCHED; writes ONE `halt` row per run, detail prefixed `gate-defer:` (fire-once latch = the carrier itself); `_warn` per tick; return 2.
  - unknown rc → **contract violation**: treat as 2 (least-irreversible disposition — never commit, never roll back) + `warn` row `gate-contract violation: …`.
- **`_gate_outcome <surface>`** — echoes `ok | fail | defer`. Every guard/success/logbook branch reads THIS token, never a carrier. This makes the omitted-site class structurally impossible at the read side.
- **Wording classes (closed vocabularies):** *definitive* labels (`/livez unreachable >Ns`, `version verify mismatch`, `soak failure`, …) go ONLY into failure carriers, byte-preserved. *Transient* labels (`livez/readyz/version verify/soak: attribution deferred (transient — budget expired)`) go ONLY into `gate_defer_reason` + the `gate-defer:` halt detail. Never cross-contaminated.

### 2.3 Per-surface disposition table (THE deliverable — §9/C3 copy this verbatim)
Legend: OS=one-shot, PL=poll; **OPEN-C**=left open WITH a named converging closer; **CLOSED-D**=closed at the surface with a distinct non-success outcome.

| # | Site (file:line @ `4a3c26e28`) | kind | Carrier-present outcome (exact branch) | Txn end-state | Recorded (existing primitives only) | Closer? (named) | Phase-2 leg |
|---|---|---|---|---|---|---|---|
| 1 | `lib.sh:8004` exhaustion arm (new) | producer | mints rc 2 once | n/a | `subprocess_wait_timeout` row, `kind=attr_deferred`, fire-once, stderr | n/a | B-contract |
| 2 | `lib.sh:8142-8143`, `:8159` | wrapper | `:8159` collapse REMOVED → rc capture; livez/readyz normalize probe-2→1 | n/a | none | n/a | B-contract + M-gate-collapse |
| 3 | `lib.sh:6103-6106` `_handback_serving_probe` | PL | `2`→probe `return 2`, keep polling (T7b) | n/a | none | verifier budget | B-defer-e |
| 4 | `promote.sh:790-794`, `:799`+`:846-848`, `:852-854`, `:861-865` | OS ×4 | `_gate_consume promote "$rc" <label>` | n/a | `gate_defer_reason` ← transient class | n/a | B-gate-1 |
| 5 | `promote.sh:873` (8a test) | read | guard = `_gate_outcome promote = ok` — **defer cannot enter commit** | OPEN-C | none | **YES** — daemon serving-green closer (P1-P9, code `:2402`/`:2442`), `intent_flip` emitter `promote.sh:574`; launcher boot twin `launcher.sh:3093` | B-gate-1 |
| 6 | NEW promote defer arm (between `:924` and `:926`) | OS terminal | `halt "gate-defer: …"` + `lock_release` + `exit 1`; **not 8b** (D4's new site) | OPEN-C | `halt` row, transient class | YES (row 5) | B-gate-1 |
| 7 | `promote.sh:926-927` (8b entry) | read | reached on `fail` only | n/a | `GATE FAILED: …` unchanged | n/a | B-gate-1 |
| 8 | `promote.sh:1059-1063`, `:1065-1069`, `:1070-1072` (rollback-via-promote re-gate) | OS ×3 | `_gate_consume promote` | n/a | as row 4 | n/a | B-gate-4 |
| 9 | `promote.sh:1084-1089` 8b bookkeeping | read | skipped when `= defer` | CLOSED-D | no `rollback` event, no counter, no quarantine | n/a | B-gate-4 |
| 10 | `promote.sh:1094-1101` REGATE halt | read | `fail` only | CLOSED-D | unchanged | n/a | B-gate-4 |
| 11 | `restart.sh:284-290`, `:292-304`, **`:321-338`** (D2's site now in scope) | OS ×3 | `_gate_consume restart "$rc" <label>` | n/a | as row 4 | n/a | B-gate-2 |
| 12 | `restart.sh:345-361` GATE_FAIL branch | read | `fail` only | CLOSED-D | existing `restart_gate`+`halt` unchanged | n/a | B-gate-2 |
| 13 | `restart.sh:363-374` success terminal | read | `ok` only — guard added before it | n/a | `restart` row only on `ok` | n/a | B-gate-2 |
| 14 | NEW restart defer arm (before `:363`) | OS terminal | `restart_gate … outcome=not-verified (transient)` + `journal_close_txn` + clear `pending_op`/`pending_restart` + `exit 1`; **no** `restart` row | CLOSED-D | `restart_gate` (existing `RESTART_TERMINAL_EVENTS` member) + close_txn — existing primitives | **NO usable** — P4 `:2402`, launcher `:3355`, reclaim `:561` all exclude restart; the two arm closers are `pending_op`-conditional (D6) ⇒ close at the surface is REQUIRED | B-gate-2 |
| 15 | `rollback.sh:267-271`, `:272-276`, `:277-279` | OS ×3 | `_gate_consume rollback "$rc" <label>` | n/a | as row 4 | n/a | B-gate-3 |
| 16 | NEW rollback defer arm (**before** `:286`) | OS terminal | `journal_set_current "$TO_VERSION"` + `set_previous` (symlink FACTS only — flip already happened), **no** `journal_count_rollback` (`:288`), `journal_close_txn`, `halt "gate-defer: … NOT VERIFIED (transient)"`, exit per the script's existing REGATE halt code | CLOSED-D | `halt` row + close_txn; **no** `rollback` event (`:298`), no cap count | **MUST NOT leave open** — D3: the closer would commit-close it wrongly (`previous=displaced_version` = the M4 strand) | B-gate-3 |
| 17 | `rollback.sh:286-289` bookkeeping, `:291-296` re-gate halt, `:298` landed row | read | bookkeeping/landed reached on `ok`/`fail` only (defer hoisted above) | CLOSED-D | unchanged | n/a | B-gate-3 |
| 18 | `lib.sh:8294`, `:8383-8387` soak ticks (D1's site — NOW WIRED) | PL | `2` ⇒ `observation_kind="INCONCLUSIVE"`; per-tick pool reset | n/a | no row; `_log` per tick | n/a | B-soak-t |
| 19 | `lib.sh:8491-8498` cap-reach | terminal | `return 2` when `inconclusive>0 ∧ no definitive red`; else `return 1` (C-1 anti-silent-commit preserved) | n/a | wording per §2.4 | n/a | B-soak-t |
| 20 | `status.sh:127-130`, `:171-174` | PL diag | `2` ⇒ `attribution deferred (transient)` note; `--verify` must NOT fail on a transient | n/a | none | n/a | B-status |

**The rows-6/14/16 asymmetry, justified:** promote leaves OPEN because `current` legitimately lags the flipped symlink and the P9-authorized closer + launcher kill-window heal resolve exactly that divergence. Restart CLOSES at the surface (no usable closer; D6 wedge hazard). Rollback CLOSES at the surface recording the symlink **fact** while withholding every success **claim** ("fact in the field, no claim in the history") — the alternative (stale `current`) creates the journal-vs-symlink divergence the promote preflight refuses on (`promote.sh:513-517`), and leaving open feeds D3's wrong-convergence.

### 2.4 Soak semantics (B3 + D1)
- **Inconclusive rule = STREAK-FREEZE:** a transient tick neither increments nor resets `consecutive_green` (`observation_kind="INCONCLUSIVE"` — a THIRD class, distinct from probe-transient RESET, which is preserved). Reset-to-0 would let a transient storm reach cap with zero greens (the forbidden outcome); increment would manufacture greens from unattributable observations. Freeze is the only reading satisfying both halves of the ruling.
- **Pool arithmetic = PER-TICK FRESH** (`_GATE_ATTR_DEFER_REMAIN` reset at the top of each soak tick's version check, before `:8383` and `:8294`). Soak cap UNCHANGED (no knob edits): the deferral spend consumes soak-window time ⇒ honest cost **+≤5 s/tick, ≤6 ticks in the 120 s adaptive cap ⇒ ≤ +30 s and −2 green opportunities worst case**; the caller's `_enforce_deadline` converts any residual overrun into a **halt — never a commit, never an auto-rollback**. Per-shell pool remains the rule everywhere else.
- **Dispositions:** all-transient or mixed (greens>0 but <N, inconclusive>0) at cap-reach ⇒ **rc 2** → consumed by `promote.sh:864` via `_gate_consume` → row-6 defer arm — 8b never reached. Genuine never-green (inconclusive==0) ⇒ **rc 1 unchanged**. Any definitive red still returns 1 immediately.

### 2.5 Missed-site fail-loud enforcement (three layers, all required)
1. **Behavioural per-surface legs** (one per mutating surface; persistent-2 fixture, REAL child pids; assert txn end-state per §2.3 incl. no-commit/no-`current`-write/no-8b-entry/no-counter-bump). Counter-mutations: restore the direct collapse at that site ⇒ leg reddens.
2. **Generated census pin** (presence-grep, labelled as such): at test time, grep the tree for the gate-family symbols; every hit must be (a) inside `lib.sh` wrappers, (b) `status.sh`, or (c) followed within 8 lines by `_gate_consume`/`_gate_outcome`. GENERATED, never a hardcoded list — the defect class is "list omits a site". Second pin: zero direct carrier reads (`gate_fail_reason|GATE_FAIL|REGATE_FAIL|gate_defer_reason`, both `$X` and `${X}` forms) outside the helper family.
3. **Runtime contradiction detector:** an `attr_deferred` marker for label L AND a definitive-failure row for L in one run = contradiction ⇒ loud `warn` + Phase-2 leg failure. Catches the site nobody censused, including shell-obfuscated reads.

### 2.6 Architect adjudications (worker E's three questions — DECIDED)
1. **Single canonical `gate_defer_reason`** — ADOPTED. Aliases struck. (If reviewer familiarity insists, read-only aliases assigned INSIDE `_gate_consume` only — never at call sites.)
2. **Rollback "fact in the field, no claim in the history"** — ADOPTED (rows 16/17). The alternative actively breaks the next promote's preflight.
3. **Closer-evidence residual** — DISCLOSED here + routed to a named follow-up lane (§8): the serving-green closer has no port-holder conjunct (a foreign port-holder is invisible to it), and D3 shows it commit-closes rollback-kind wrongly. Both are daemon-side; this lane changes zero daemon code.

---

## 3. Spec deltas

**phase1-plan.md:** §5.4b → REWRITE to §2.1-2.3 (single carrier; `_gate_consume`/`_gate_outcome`; 0/1/2 table incl. the probe-2 asymmetry decision; alias text struck). §5.4a per-site table → §2.3. §9 commit scoping → ADD exactly: `promote.sh:790,793,799,846-847,852-853,861,864,873,924-927,1059-1072,1084-1101`; `restart.sh:275,284-290,292-304,321-338,345-361,363-374`; `rollback.sh:266,267-279,286-296,298`; `lib.sh:8004-8118,8142-8143,8159,8206,8236-8252,8294,8383-8387,8400-8450,8477-8498`; `status.sh:125-130,171-174`. §10: T7d→T7d-v2 (helper family); NEW T7e (status.sh), T7f (soak freeze+cap-reach rc), T7g (census pin). §11: C3 copies §2.3's file:line column; NEW C3b (status.sh alone). §12 R7 rewrite; §13 S14 rewrite to txn end-states; NEW S15/S16. §14: I-B13 rewrite (rc=2 = inconclusive); NEW I-B14 (no direct carrier read outside the helper family); NEW I-B15 (restart/rollback defer arms CLOSE at the surface — never leave open). Blast-radius line: "no new halt machinery" → **DROPPED** (D4).
**phase2-plan.md:** legs `B-gate-1..4`, `B-soak-t`, `B-status`, `B-census`, `B-contract` (unknown-rc); counter-mutations `M-gate-collapse`, `M-gate-read`, `M-soak-reset`, `M-soak-pershell-poison`; SC-12/13/14 re-cut to "no commit row; `current` unchanged-or-fact-only; no `rollback`/`restart` success row; no 8b entry; txn end-state per §2.3; closer named per surface".
**phase3-plan.md:** **UNTOUCHED** — no row touches `stage.sh`, the manifest, or the tag/floor graph; `rollback_safe=true` stands. CHANGELOG disclosure set (D-P3-3) grows by ONE item (status.sh `--verify` transient loosening) → six items.
**decisions.md:** corrected-premise record (§0 above); NEW binding **D-P1-10 closer-liveness rule** (per-surface: closer named or close-distinct; P9 is per-target; restart never closer-eligible at runtime, rollback conditionally but wrongly ⇒ always close-distinct); STRIKE D-P1-8(i)/(vi) alias + universal-closer text; D-P1-4 per-consumer framing → one-writer/one-reader family; record the probe-2 asymmetry as a decision; anchor corrections from §1.

## 4. Rejections (strike from the current plan text)
1. §5.4b "consumed by the EXISTING B4 leave-txn-open halt path" — false (upstream-only).
2. "Recovery via the sweep serving-green closer" as a universal route — true for promote ONLY.
3. T7c's soak-cap reading ("per-tick 2 = no-green-this-tick") — the B3 mechanism; replaced by INCONCLUSIVE/freeze.
4. "No new halt machinery" — dropped (D4; three new terminal arms + a read-side token).
5. T7d's grep-only acceptance — replaced by behavioural legs + generated census.
6. The three-alias carrier requirement — struck (single name).
7. `promote.sh:1094` "version mismatch on previous" / `rollback.sh:298` landed row reachable from rc=2 — asserted unreachable, pinned.

## 5. Residuals (cannot be closed in this lane — disclosed, routed)
1. **[medium] Serving-green closer evidence**: no port-holder conjunct (a persistent-2 promote txn converges commit-green on weaker evidence than the gate that halted it) AND commit-closes stale rollback-kind wrongly (D3). Daemon-side; routed to a named follow-up lane (project future list: *closer evidence hardening — port-census conjunct + rollback-kind exclusion*). This lane: disclose in ceremony notes.
2. **[low]** `restart.sh` has no distinct exit code for "not verified" (0/1/78 only) — defer arm uses `exit 1` + distinct rows; a real rc-vocabulary change would audit `_journal_executor_exit`.
3. **[low]** Census layer-2 is presence-grep on the call side — covered by the second grep pin + layer-3 detector.
4. **[low]** `status.sh --verify` loosening — disclosed (CHANGELOG item 6).
5. **[low]** Rollback onto a never-promoted release: no `intent_flip` in history ⇒ no closer ever — correctly handled by close-distinct; the operator gets a halt for a possibly-healthy rollback with no automatic convergence.
6. **[low] `adopt-unit.sh:453` un-attributed green (D5)** — pre-existing; register explicitly out-of-scope; rides the follow-up lane.

## 6. Verdict
The design closes all three blockers **at the root rather than at the wiring**: B1 by giving the carrier its own terminal arms at the three surfaces' actual post-flip decision points with a one-token read side; B2 by the closer-liveness rule grounded in the verified per-target P9 / restart-excluded P4 semantics (promote OPEN-C, restart/rollback CLOSED-D); B3 by INCONCLUSIVE/freeze + per-tick pool + cap-reach rc 2, with D1's unwired soak-tick site now wired. The single-writer/single-reader helper family + generated census + runtime contradiction detector is the structural answer to "omitted site ⇒ wrong default". Confidence: **high** — every disposition is code-anchored at `4a3c26e28`, every load-bearing fact double-verified (D and E independently found the per-target P9 and the per-shell pool poisoning). Assumption to re-verify post-merge: the census (generated pin re-run after the D-P3-1 sync-merge).
