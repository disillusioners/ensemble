# P2.3 B6b — R3.2 journal reset (front-loaded) + DR-4(c)/(d) — **STOPPED at plan-level blockers before (c)/(d) execution**

- **Date:** 2026-08-23 · **Recorded by:** worker (B6b dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `c0993119` (runbook @ same HEAD; corrected runbook executed as written for the parts that were code-executable)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §5 R3.2 (mandatory post-DR-4 journal reset)
- **Verdict lines:**
  - `R3.2 PASS: journal reset executed on the real demo journal — exact-path assert, archive-then-fresh (md5-preserved archive kept), fresh init via dr4reset stage, counters 24h=0/cooldown null/quarantined []/history [] (halt CLEARED), daemon serving untouched (e2e2/0.10.5); ledger_check.py FIRST REAL-JOURNAL run captured verbatim (exit 0, cycles 0, gate BLOCKED F2-open per §9); live untouched`
  - `DR-4(c) STOPPED: plan-blocked PRE-EXECUTION — the front-loaded reset leaves journal previous=null, and promote 8b's auto-rollback gates on previous FIRST (halt(no-previous): daemon rests on the failed target, NO rollback/quarantine/counter) — leg dr4b1 as dispatched would strand the demo degraded instead of producing the rollback evidence; per task rule (anomaly → STOP, don't improvise) the legs were NOT run`
  - `DR-4(d) STOPPED: plan-blocked PRE-EXECUTION — after (c) arms the cap (count=3), promote_entry_check refuses entry (reason=cap, exit 78) BEFORE journal_open_txn, so the runbook's literal orphan induction ("run a promote … SIGKILL promote.sh after the flip step") cannot create the stale in_flight; no code-faithful promote-path creation exists under an armed cap`

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/b6b-ev/` (redaction applied at capture time). Demo port 7979 is not restricted.

---

## 1. DR-0 inline re-inventory (FL-1; minted inline per §0.2 — demo state changed in B6a)

| Item | Observed (19:47:26–19:47:58Z) | Match vs expectation |
|---|---|---|
| Script-version checkpoint (R3.5) | HEAD `c0993119194480c9914f66033041c8dbfe06a0a7`; `lib.sh 77cab857…` · `promote.sh 36a8f2ec…` · `stage.sh 3d02f209…` · `status.sh 2ff4845b…` · `rollback.sh 9d86836e…` · `ledger_check.py a1b636f2…` · `launcher.sh 1e6a35fc…` (repo launcher == dr4a payload sha, B6a §2.5) | ✓ recorded (`/tmp/b6b-ev/script-versions.txt`) |
| `status.sh demo` triple + journal | `target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo`; journal healthy; `current -> releases/v0.10.5-p2.1-e2e2`; lock free; `/livez version=0.10.5`; `version smoke: OK` | ✓ §0.1 |
| Demo probes | `/livez` 200 `{"status":"alive",…,"version":"0.10.5"}` uptime 852.9s ⇒ born 19:33:25Z = B6a (b)'s rollback restart; `/readyz` 200 `reasons:[]` | ✓ green baseline |
| **Demo family (re-derived, lstart-verified)** | wrapper **81333** (`bash scripts/upgrade/promote.sh demo`, ppid 1 — F-DR1-5 class residue of B6a (b)'s promote) → launcher **81334** (`/bin/bash ./launcher.sh`) → bootloader **81378** → daemon **81382** (listener 127.0.0.1:7979, cwd = demo install); lstart all `Mon Aug 24 02:33:17 2026` local (+0700) = 19:33:17Z | ✓ B6a §5 handoff family exactly |
| **Live pid baseline (start)** | listener pid **31150** / ppid **31130**, lstart `Sat Aug 22 10:04:07 2026`, `./ensemble-prod` — resolved read-only (port from live install's own `.env`; lsof sed-redacted to `<live-port>` BEFORE capture) | ✓ all-day baseline |
| Demo `.env` | md5 `1ba30c018078a60281cba4baeacc03c4` (== B6a baseline); knob line count 0; 5 `POSTGRES_*` part lines | ✓ |
| `.launcher-state` | `last_exit=75 crash_count=4 window_start=1787507522 last_backoff=60 notified_75=1 last_uptime=2` | ✓ byte-identical to B6a's record (no writes this batch — no daemon restarts happened) |
| **Journal PRE-RESET snapshot** (read-only cp, md5 `8b2bf6dcdec03f66f6c223d3aa4ead73`) | `current=e2e2, previous=dr4a, in_flight:null`; `rollback_window_count {24h: 3, window_start: 2026-08-23T19:33:22Z}`; `cooldown_until 2026-08-23T19:43:22Z` (expired ~4min before capture); `quarantined [v0.10.5-p2.1-e2e-bad2, v0.10.6-p2.3-dr4b]`; 12 history events; halt events at 22:41:33Z (P2.1 e2e residue) + **19:33:24Z (cap, B6a (b))** | ✓ exactly the declared R3.2 input state |
| Releases on disk pre-reset | 4 dirs: `e2e-bad2 [QUARANTINED]`, `e2e2`, `dr4a`, `dr4b [QUARANTINED]` | ✓ B6a §5 |

---

## 2. R3.2 journal reset — transcript

| Step | Command / check | Result |
|---|---|---|
| 1. Pre-touch asserts | `status.sh demo`: in_flight null, lock free (§5 R3.2 step 1) | ✓ (§1) |
| 2. **EXACT-PATH assertion** | `$JP = /Users/nguyenminhkha/agents-ensemble-demo/releases/state.json`; case-anchored under `$HOME/agents-ensemble-demo/`; explicit NOT-under-`$HOME/agents-ensemble/` check (§0.6 anchored compare, not name matching) | ✓ PASS before ANY touch |
| 3. **Archive-then-fresh** | `mv state.json → state.json.archive-dr4-20260823-1948` (mv, never rm -rf); canonical path verified ABSENT after | ✓ archive md5 `8b2bf6dcdec03f66f6c223d3aa4ead73` == pre-reset snapshot |
| 4. Script smoke post-archive | `bash scripts/upgrade/promote.sh demo --help` | exit 0 (arg-parse path, no journal op) |
| 5. Tag rotation (FL-2) | deleted `v0.10.6-p2.3-dr4a` + `v0.10.6-p2.3-dr4b`; created `v0.10.6-p2.3-dr4reset` @ c0993119; `git describe --tags --exact-match HEAD` → `v0.10.6-p2.3-dr4reset` ✓ | ✓ single-tag HEAD |
| 6. Fresh init via stage | `VERSION=v0.10.6-p2.3-dr4reset ENSEMBLE_BINARY_VERSION=0.10.5 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo` (19:48:28→19:48:43Z, 15s, exit 0) — **binary REUSED** (`dist/ensemble-prod` sha `fd7c1ac0efd4dbfeaf7a11f0cf375efdce5c79051e0a0be5f4032d18879b9f0c` == B6a's fresh real build; "using existing binary"; B6a FL-4 intra-set precedent, friction-logged §8) | ✓ journal auto-init by `journal_init` |
| **Override uses (recorded per D-FA4.5)** | `ENSEMBLE_ROLLBACK_SAFE=1` ×1 (dr4reset stage — drill-set intra-set rollback override; derived would be `false` via `contains_contract_phase=true`); `ENSEMBLE_BINARY_VERSION=0.10.5` ×1 (drill-tag ≠ `daemon/__init__.py` `0.10.5`; B6a FL-4) | both stage.sh author-call knobs |
| 7. **Post-reset verification** | raw journal: `{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}` — counters **0/0**, cooldown **null**, quarantined **[]**, history **[]** ⇒ **halt CLEARED**; `current -> releases/v0.10.5-p2.1-e2e2` unchanged; `/livez` 200 v0.10.5 (uptime 931s — same daemon, no restart); `version smoke: OK` | ✓ all four R3.2 verify clauses |

Post-reset releases on disk (5 + archive): `e2e-bad2`, `e2e2`, `dr4a`, `dr4b`, `dr4reset` + `state.json` + `state.json.archive-dr4-20260823-1948`. **[op-notice]** the fresh journal's `quarantined:[]` also cleared the `[QUARANTINED]` labels status.sh renders for `bad2`/`dr4b` — quarantine is journal state, not disk state; re-promoting those versions would no longer be refused `quarantine` until they fail a gate again. Inherent to archive-then-fresh; not touched further (neither is a target in this batch).

### 2.1 `ledger_check.py` — FIRST REAL-JOURNAL RUN (post-reset, mandated) — verbatim

```text
2026-08-23T19:49:06Z — uv run python scripts/upgrade/ledger_check.py \
    --journal ~/agents-ensemble-demo/releases/state.json --f2-state open   (exit 0)

ledger-check: journal=/Users/nguyenminhkha/agents-ensemble-demo/releases/state.json
f2-state: open
cycles: 0
staleness: none (all cycles at the current version)
current version: None
consecutive clean: 0 (need 3, ADR-021)
gate verdict: BLOCKED
  - F2-open: the unauthenticated loopback API user-origin forge lane is open — gate hard-blocked regardless of cycle count (runbook §9)
note: coverage: journal-checkable clauses of test-strategy.md 4.1 only; clauses 3-5 (readiness log-scan, work-loss resume evidence, live-pid checkpoint) are external evidence audited in RESULTS files — the gate consumer folds both
```

**Note vs dispatch wording:** the task expected "zero cycles, NOT-READY, f2-open BLOCKED line". With `--f2-state open` the checker's verdict token is **BLOCKED** (the §9 hard block, regardless of count); **NOT-READY** is the f2-closed + count<N verdict (`ledger_check.py gate()`, :220-254). Zero cycles ✓, the F2-open hard-block line ✓ — captured verbatim above; the checker's own token is authoritative.

### 2.2 Supplementary (disclosed, read-only): checker on the ARCHIVED pre-reset journal

Run against the archive file (post-archive, after the mandated run) to document the checker's real-history classification on B6a's residue:

```text
cycle 1: version=v0.10.5-p2.1-e2e1 txn=2026-08-22T22:24:05Z verdict=SUPERSEDED
cycle 2: version=v0.10.5-p2.1-e2e2 txn=2026-08-22T22:40:41Z verdict=SUPERSEDED cause=halt@2026-08-22T22:41:33Z,rollback@2026-08-22T22:42:58Z
cycle 3: version=v0.10.5-p2.1-e2e1 txn=2026-08-22T22:48:29Z verdict=SUPERSEDED cause=rollback@2026-08-22T22:49:25Z,rollback@2026-08-22T22:49:26Z
cycle 4: version=v0.10.6-p2.3-dr4a txn=2026-08-23T19:18:27Z verdict=VIOLATION cause=rollback@2026-08-23T19:33:22Z,halt@2026-08-23T19:33:24Z
staleness: reset — count re-entered at cycle 4 (version changed v0.10.5-p2.1-e2e1 → v0.10.6-p2.3-dr4a); superseded cycles: 1,2,3
current version: v0.10.6-p2.3-dr4a
journal current: v0.10.5-p2.1-e2e2
consecutive clean: 0 (need 3, ADR-021)
gate verdict: BLOCKED   [- F2-open … hard-blocked regardless of cycle count (runbook §9)]
```

4 cycles derived from 12 real events; the dr4a commit window correctly classified VIOLATION with B6a (b)'s rollback+halt as causes; staleness reset line present. Live-path refusal sanity: `--journal ~/agents-ensemble/releases/state.json` → **exit 78** `REFUSED: … resolves under the live install root` (fires before any read) ✓.

---

## 3. STOP — DR-4(c)/(d) plan-level blockers (code-verified PRE-execution)

Both blockers were found by source-reading the pipeline **before any (c)/(d) mutation**; per the dispatch rule (*anomaly → STOP, report, don't improvise*) the legs were not attempted. Code citations at HEAD `c0993119`.

### 3.1 Blocker C1 — DR-4(c) legs cannot run on the just-reset journal: `previous=null` ⇒ first leg HALTS (no-previous), no rollback evidence, demo left degraded

- `journal_init` (lib.sh:~600) writes the fresh journal as `{"current":null,"previous":null,…}` — the R3.2 reset **zeroes `previous`**. Re-anchoring happens only on the next **commit** (runbook §5 R3.2 note: "journal `current`/`previous` re-anchor on the next **commit**").
- The (c) legs are **rollback** legs — they never commit. Promote 8b (auto-rollback) gates on `previous` **FIRST**: `PREV` null → **halt(no-previous)** branch (promote.sh:259-273): `journal_close_txn; set_current($VERSION); set_previous(null); halt "gate fail … with NO previous release — halt-for-human, daemon rests on $VERSION (degraded, alerted)"` — exit 78. **NO repoint, NO quarantine, NO counter increment, NO cooldown** — exactly the evidence the (c) legs exist to produce.
- Concretely for leg dr4b1 as dispatched: knob-induced `/readyz` gate-fail → demo rests on the flipped dr4b1 with readyz 503 until a manual restart (knob lives in the process env — P7 restore-is-restart semantics), journal `current=dr4b1`, a *no-previous* halt event, and the dispatcher's expected `rollback+quarantine+count` evidence absent. Recovery would be manual (unscripted) improvisation.
- The runbook never runs rollback legs on a fresh-reset journal (its R3.2 sits AFTER all four legs; B6a's (b) ran on a journal with `previous=dr4a` non-null from (a)'s commit). The front-load (correct for unblocking entry) created a state the (c) procedure was never shaped for. **The dispatcher's "cap legs then run on the clean journal" missed that "clean" includes `previous=null`.**

### 3.2 Blocker C2 — DR-4(d)'s orphan induction is impossible after (c) arms the cap: entry-side cap refusal fires BEFORE any txn opens

- (c)'s pass state is `count=3` + halt armed (that is the deliberate terminal state for B6c). `promote_entry_check` (lib.sh:1249-1256) refuses `reason=cap` (exit 78) whenever `journal_rollback_count_24h ≥ 3` — and promote.sh invokes it at step 1d, **before** `journal_open_txn` at 1f (promote.sh:123-158). A refused promote opens **no** txn, flips **nothing** — there is no "after the flip step" to SIGKILL at.
- Therefore the runbook §5(d) literal induction ("run a promote against a version-lie target … SIGKILL `promote.sh` after the flip step") **cannot create the stale in_flight** once the cap stands — under ANY wait/cooldown schedule (the cap is the refusing gate, not the cooldown; the 24h window rolls over only 2026-08-24).
- The sweep's own cap-immunity (D-FA4.2 — correctly asserted by the dispatch: recovery never refuses) is not in question; what is blocked is **producing the sweep's input** via the promote path. This ordering wall exists inside the runbook itself (its (d) follows its (c) on one journal); the front-loaded reset did not create it, only inherited it.

### 3.3 Resolution options (pre-analyzed for the dispatcher — NOT executed)

- **Option A — minimal insertion, code-faithful (recommended):**
  1. After the (already-done) reset: **two clean commits to re-anchor** `current`/`previous` — e.g. promote `dr4a` (commit 1: `current=dr4a, previous=null`) then promote `e2e2` (commit 2: `current=e2e2, previous=dr4a`); both known-good, both gates+300s-soak green (~11 min added wall-clock; retention keep-3 will start evicting — pinned pair safe per T8).
  2. Then (c) legs dr4b1/b2/b3 exactly as dispatched (auto-rollback onto non-null `previous`; count 1→3; cap halt on #3), 4th-promote (dr4c) refusals ×3.
  3. (d) via the **rollback.sh orphan induction** — the only cap-legal creation path (rollback.sh is the recovery lane: "manual rollback is NOT subject to cooldown/cap entry refusal itself" — rollback.sh:18-19, D-FA4.2): stage a throwaway `dr4d` (`rollback_safe=true`), `bash scripts/upgrade/rollback.sh demo --to v0.10.6-p2.3-dr4d`, SIGKILL post-`journal_mark_flipped` (rollback.sh:141-149) ⇒ orphan `in_flight{kind:rollback, target=dr4d, flipped:true}`; sleep 610; restart ⇒ launcher sweep treats any non-restart kind identically (launcher.sh `_journal_sweep`: kind≠restart, stale, flipped → sweep-rollback branch) ⇒ `sweep_rollback` event, count 3→4 (**past the cap, never refused** — the exact D-FA4.2 assertion), quarantine dr4d, env restored to previous, halt re-armed event at count≥3. **Disclosed deviations vs runbook text:** (i) two extra clean commits; (ii) (d) induction via rollback.sh, not promote.sh.
  4. Total added wall-clock ≈ 75–85 min (2 soaks + 3×600s cooldowns + 610s staleness sleep).
- **Option B — runbook-literal (d), re-ordered:** (c) legs 1–2 (count 2) → (d) promote-orphan + sweep (entry passes at count 2; sweep count 2→3 **arms** the cap via the sweep's own halt event) → 4th-promote refusals. Cost: only TWO knob-rollback legs (not three as dispatched); "runs PAST the halt state" becomes "arms the halt" (weaker D-FA4.2 proof). Evidence shape deviates from the dispatch's pass criteria.
- **Option C — dispatcher rules otherwise** (e.g. wait for the 24h window rollover 2026-08-24T~19:33Z+ and re-run (c) on the pre-B6b journal — moot: this batch's reset already archived it; or reshape the batch).

---

## 4. Journal events verbatim (this batch's writes)

This batch wrote **no journal history events** (reset = archive + fresh init; stage triggers init only). Pre-reset tail (archived journal, md5 `8b2bf6dc…`) for continuity:

```text
2026-08-23T19:18:27Z | commit    | promote v0.10.6-p2.3-dr4a committed (gate+soak green; previous=v0.10.5-p2.1-e2e2)
2026-08-23T19:33:22Z | rollback  | auto-rollback v0.10.6-p2.3-dr4b → v0.10.5-p2.1-e2e2 (gate fail: /readyz unreachable >120s; re-gate green)
2026-08-23T19:33:23Z | quarantine| v0.10.6-p2.3-dr4b quarantined after gate failure (skipped by future promotes)
2026-08-23T19:33:24Z | halt      | rollback cap 3/24h reached (count=3) — halt-for-human; promotes refused until the window resets
```

Post-reset journal (complete, verbatim): `{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}`

## 5. Cooldown timeline

No cooldown was armed by this batch (no rollback ran). The pre-reset cooldown (`19:43:22Z`) expired before DR-0 capture and was annulled by the reset (fresh `cooldown_until:null`). N/A for the stopped legs.

## 6. Live pid checkpoint table (read-only; zero live contact)

| Checkpoint | Moment (UTC) | pid/ppid | lstart | Diff vs start |
|---|---|---|---|---|
| start (§0.4) | 19:47:26 | 31150 / 31130 | Sat Aug 22 10:04:07 2026 | — (baseline) |
| end (post-reset + checker, close) | 19:49:41 | 31150 / 31130 | same | **identical ✓** (`PS-IDENTICAL` + `LSOF-IDENTICAL` redacted) |

Resolve method per §0.4: port from live install's own `.env`; lsof lines sed-redacted to `<live-port>` before capture. No signal, no HTTP, no write to live at any point.

## 7. Constraint compliance

- **Live:** READ-ONLY throughout; invariance proven §6 at both checkpoints. Live-pid-checkpoint invariant satisfied.
- **Demo:** mutations exactly the R3.2 set — 1 archive (mv), 1 stage (dr4reset), 0 promotes, 0 restarts (daemon uptime continuous across the whole batch: born 19:33:25Z, alive at 19:49). `ENSEMBLE_ROLLBACK_SAFE=1` used exactly once (dr4reset stage, recorded §2). `ENSEMBLE_UPGRADE_LIVE` never set. No stop-script invocations. `.env` md5 `1ba30c01…` bit-exact before/after (stage's marker sed was a no-op).
- **PID discipline:** zero process signals of any kind (no stops, no kills) — nothing needed one.
- **Repo:** single artifact written (this file). `git status --porcelain` at close: ` M .agents/approver/active.md` (pre-existing, untouched) + `?? .agents/tester/RESULTS/2026-08-23-p2-3-b6a-dr4ab-promote-rollback.md` (B6a's, untracked) + this new file. **NO commits.** Local tags: `v0.10.6-p2.3-dr4a`, `v0.10.6-p2.3-dr4b` (deleted for rotation, re-created at close per B6a precedent), `v0.10.6-p2.3-dr4reset` (new, authorized drill tag) — all at `c0993119`.
- **Port literals:** zero live-port literals in this file or evidence files (capture-time redaction). Demo 7979 only.

## 8. Operator friction log (T1 signal)

| # | Where | Doc/dispatch says | Observed | Classification |
|---|---|---|---|---|
| FL-1 | dispatch step 3 vs code | "(c) cap legs then run on the clean journal" | Clean journal = `previous:null`; promote 8b's no-previous branch HALTS leg 1 (daemon rests degraded on the failed target; no rollback/quarantine/counter evidence). Rollback legs require a prior **commit** to anchor `previous` — the runbook's own flow always had one ((a)); the front-loaded reset removed it | **MAJOR plan gap** (blocker C1, §3.1) — runbook §5 R3.2 note says "re-anchor on the next commit" but never states that rollback legs need it |
| FL-2 | runbook §5(d) vs code | "run a promote … SIGKILL promote.sh after the flip step" | Entry-side cap refusal (1d) precedes txn open (1f): under count=3 there is no flip to SIGKILL past. The (d) induction is unexecutable after (c) as ordered — and equally unexecutable in the runbook's own (a)(b)(c)(d) order | **MAJOR plan gap** (blocker C2, §3.2) — runbook needs an explicit creation path that is cap-legal (rollback.sh induction) or a re-order |
| FL-3 | dispatch step 2 expected checker output | "expect: zero cycles, NOT-READY, f2-open BLOCKED line" | Checker emits verdict **BLOCKED** (F2-open hard block) — `NOT-READY` is the f2-closed verdict token. Zero cycles ✓, hard-block line ✓ | **MINOR wording** — dispatch conflated the two verdict tokens; checker authoritative (§2.1) |
| FL-4 | stage binary choice | "real-build reuse per B6a's FL-4 friction precedent OR fresh" | REUSED `dist/ensemble-prod` (sha `fd7c1ac0…`, B6a's fresh build; intra-set byte-identical payload discipline). Fresh build NOT needed — no forward-ref proof rides on this batch | **CHOICE, friction-logged** (as instructed) |
| FL-5 | reset semantics observation | runbook: "quarantined []" listed as a verify clause | Clearing the journal also un-quarantines `bad2`/`dr4b` on disk (labels gone from status.sh) — quarantine is journal state. Harmless here (neither is a target), but a future re-promote of a known-bad version would NOT be refused until it fails a gate again | **OK-but-noted** (§2 op-notice) |
| FL-6 | harness | — | Pipeline `$?` capture must use `${PIPESTATUS[0]}` — a `| head` line reported head's exit (0) for the checker's 78 until re-run unpiped | **executor technique, carried** |

**Friction summary:** the R3.2 reset procedure executed exactly as documented (all four verify clauses green, archive preserved). The two MAJOR items are plan-level sequencing gaps between the dispatched order and the pipeline's code (C1/C2) — both found by pre-execution source reading, both STOPPED per the anomaly rule, both with pre-analyzed options (§3.3).

## 9. Findings

1. **F-B6b-1 (blocker, binding for any B6b-continue):** the (c) legs need a non-null, `rollback_safe=true`, non-quarantined journal `previous` — i.e. ≥2 clean commits after the reset (first commit anchors `current`, second anchors `previous`). Option A §3.3. Until ruled, do NOT promote any gate-failing target on the reset journal (no-previous halt strands the demo degraded).
2. **F-B6b-2 (blocker):** under an armed cap (count=3), the only code-legal creator of a stale flipped `in_flight` is `rollback.sh` (D-FA4.2 recovery lane — cap/cooldown-exempt by design, rollback.sh:15-19); the launcher sweep then treats `kind:rollback` identically to `kind:promote` (non-restart kinds share the decision table). The runbook's (d) text should name it as the cap-legal alternative induction.
3. **F-B6b-3:** checker first real-journal run is faithful to design: fresh journal ⇒ 0 cycles, BLOCKED(F2-open) regardless of count; archived 12-event journal ⇒ 4 cycles with correct VIOLATION/SUPERSEDED/staleness derivation; live-path refusal fires exit 78 pre-read. The B2 checker is fit for §7 ledger duty.
4. **F-B6b-4:** the reset's `previous:null` also means `status.sh`'s journal `current` is null while the daemon serves e2e2 (symlink-resolved independently) — preflight 1e skips CURRENT-integrity when `current:null` (promote.sh:130). Transient and self-healing on the next commit; recorded so a future reader does not misread it as divergence.
5. **F-B6b-5:** cooldown realism for any continue-batch: Option A needs ~3×600s cooldown waits + 2×300s soaks + 610s staleness sleep ≈ 75–85 min wall-clock; the 24h window (now `window_start:null`) re-arms from the first counted rollback of the continue-batch.

## 10. Verdicts

`R3.2 PASS: journal reset executed on the real demo journal — exact-path assert, archive-then-fresh (md5-preserved archive kept at releases/state.json.archive-dr4-20260823-1948), fresh init via dr4reset stage, counters 24h=0 / cooldown null / quarantined [] / history [] (halt CLEARED), daemon serving untouched (e2e2 / 0.10.5, no restart); ledger_check.py FIRST REAL-JOURNAL run captured verbatim (exit 0, cycles 0, gate BLOCKED F2-open per §9; supplementary archive run shows correct VIOLATION/SUPERSEDED classification; live-path refusal exit 78); .env bit-exact; live untouched`

`DR-4(c) STOPPED: plan-blocked PRE-execution (blocker C1) — front-loaded reset leaves previous=null; promote 8b halts no-previous on leg 1 (daemon rests degraded, no rollback/quarantine/counter evidence); legs NOT run per anomaly→STOP; needs dispatcher ruling (Option A: 2 re-anchor commits, then legs as dispatched)`

`DR-4(d) STOPPED: plan-blocked PRE-execution (blocker C2) — with count=3, promote_entry_check refuses (reason=cap) before journal_open_txn, so the runbook's promote+SIGKILL orphan induction cannot create the sweep's input; needs dispatcher ruling (Option A: rollback.sh-based cap-legal induction, sweep still proves recovery-past-cap 3→4)`

---

# CONTINUE-BATCH — Option A APPROVED (dispatcher ruling, 2026-08-23 19:5xZ)

**Ruling:** Option A (§3.3) approved; B6c re-sequenced (T8 SSE from standing halt → R3.2-bis final reset + checker → evidence commit — B6c counterpart's scope). The two MAJOR plan gaps (F-B6b-1/2) ride **B8's runbook fix** per ruling — recorded as the runbook-fix pointer (F-B6b-9 below), not patched here. Executed 19:56:43 → 20:54:58Z (~58 min).

**Verdict lines (continue-batch):**
- `DR-4(c) PASS: 3 fresh-version cap-exhaustion legs (dr4b1/b2/b3, knob induction, TRIGGER-1 clearance each) — rollback+quarantine+counter each (0→1→2→3), honest cooldowns (545s+563s waits), cap halt armed at #3 (20:40:35Z); dr4c staged first, 4th promote ×3 → exit 78 reason=cap at preflight, 3 journaled refusals, halt standing, zero side effects, NO halt_ack; live untouched`
- `DR-4(d) PASS: sweep executed past cap (4→5), D-FA4.2 proven literally — entry-side promotes refused at count=3 (3× journaled reason=cap) while BOTH recovery lanes executed past it: manual rollback 3→4 (never refused) AND launcher sweep 4→5 (stale-lock break heartbeat 619s/owner-dead → sweep_rollback 20:54:12Z → sweep halt 20:54:14Z); env restored green on dr4r1; live untouched`

## 11. CB-0 — DR-0 inline refresh (19:56:43Z)

Journal clean (post-reset verbatim: counters 0/null/[]/[]); demo green e2e2 (`/livez` v0.10.5 uptime 1402s, `/readyz` ready []); `current → releases/v0.10.5-p2.1-e2e2`; live 31150/31130 lstart `Sat Aug 22 10:04:07 2026` — **identical to batch start** ✓.

## 12. C1 closure — 2 re-anchor clean commits (per approved Option A step 2)

| Leg | Tag (FL-2 rotation) | Stage | Promote (FULL: gates + 300s soak honest) | Journal after |
|---|---|---|---|---|
| r1 | `v0.10.6-p2.3-dr4r1` (dr4a/dr4b/dr4reset rotated out; single-tag HEAD) | 15s exit 0; binary REUSED `fd7c1ac0…` (ruling-authorized, B6a FL-4 intra-set precedent) | 19:57:19→20:02:34Z (315s): preflight ✓ stop ✓ flip ✓ livez ~2s ✓ readyz same-round ✓ version verify 0.10.5 ✓ **soak 300s green** ✓ → **exit 0** (`PROMOTE_EXIT` line lost to the tee-pipe hold, FL-10; exit-0 independently proven by the commit event + `in_flight:null` + lock free + "promote complete" line) | `current=dr4r1, previous=null`, count 0, 1 event (commit 20:02:34Z); retention evicted e2e2/bad2/dr4a (6>keep-3) |
| r2 | `v0.10.6-p2.3-dr4r2` | exit 0; reused | 20:05:5x→20:11:23Z: all gates ✓ soak ✓ → **`PROMOTE_EXIT=0` captured** (pipe-free redirect technique, FL-10 fix) | `current=dr4r2, previous=dr4r1` — **previous anchored ≠ null (C1 CLOSED)**; retention evicted dr4b (4>keep-3) |

**[op-notice, FL-3-class executor artifact, disclosed]** between r1 and r2: my `proc_stop` of the r1 promote wrapper TERMed the wrapper's **process group** — `nohup` without `setsid` stays in-group — killing the freshly-committed daemon tree at 20:03:35Z (graceful 143; demo dark 20:03:35→20:05:43Z ≈ 2min08s). NOT a pipeline event (journal clean, commit durable). Recovery: documented manual relaunch (nohup shape); committed dr4r1 booted green. `.launcher-state` untouched (byte-identical, same as B6a's TERM-path observation). Friction FL-7.

## 13. DR-4(c) — cap-exhaustion legs (fresh version per leg, F1/FL-2 rotation)

Per-leg shape (identical, all disclosed): tag rotate → stage (`ENSEMBLE_ROLLBACK_SAFE=1` + `ENSEMBLE_BINARY_VERSION=0.10.5`, binary reused) → knob induction (`.env` backup; trailing-newline pre-check; `ENSEMBLE_READINESS_FORCE_DEGRADED=1` appended; knob-set md5 `41a18a72…` each time) → promote (pipe-free) → **TRIGGER-1 watcher** (target `/readyz` 503 observed → knob cleared same second) → gate budget exhaust → auto-rollback → verify.

| Leg | Promote window | TRIGGER-1 clear | Rollback target (ping-pong) | Journal events | Count | Cooldown armed→expired (honest wait) |
|---|---|---|---|---|---|---|
| dr4b1 | 20:12:32→20:15:01Z (149s) | 20:12:51Z (503 forced-reason observed; `.env` md5 back to `1ba30c01…` same second) | previous=**dr4r1** (CUR=dr4r2→prev) | rollback 20:15:01 + quarantine 20:15:01 + rollback-complete (count 1/3) | 0→**1** | 20:15:01→20:25:01Z (slept 545s → promote at 20:25:16Z) |
| dr4b2 | 20:25:16→20:27:45Z | 20:25:37Z | previous=**dr4r2** (CUR=dr4r1→prev) | rollback + quarantine + rollback-complete (2/3) | 1→**2** | 20:27:45→20:37:45Z (slept 563s → promote at 20:38:01Z) |
| dr4b3 | 20:38:01→20:40:35Z | 20:38:23Z | previous=**dr4r1** | rollback 20:40:34 + quarantine 20:40:35 + **halt (cap 3/24h, count=3)** 20:40:35 — cap branch replaces the rollback-complete line (B6a FL-7 confirmed on a truly-fresh window) | 2→**3** | 20:40:33→20:50:33Z (standing; see refusals note) |

Each leg: promote exit **1** (rolled back, env recovered); re-gate `rollback livez OK`+`readyz OK`; post-leg `/readyz` ready []; `.env` bit-exact `1ba30c01…` (knob 0 lines). **Ping-pong verified as pre-derived** (§3.1 analysis): `previous` alternated dr4r1/dr4r2 — never quarantined, always `rollback_safe=true` — the C1-closure anchoring held through all three legs. Retention (legs 1–2, non-cap rollback-complete path): evicted `dr4reset` then `dr4b1` (4>keep-3, pinned pair safe; quarantined releases ARE evictable by design — evidence preserved in the archive journal + this file; friction FL-11).

### 13.1 4th-promote refusals (halt stability ×3)

`dr4c` staged FIRST (not-staged refuses before the cap check — wrong reason), then 3 promote attempts 20:41:14/16/17Z: **all exit 78**, shell token `reason=cap`, **each journaled** (`refusal … (reason=cap)` at 20:41:15/17/19Z — P2.3 refusal journaling live). Zero side effects: `current → dr4r1` unchanged, `in_flight:null`, lock free, knob 0 lines. **Halt standing — NO halt_ack** (human-only; left for B6c's T8 input). Note: the leg-3 cooldown (until 20:50:33Z) was still active during the refusals — the cap check precedes cooldown in `promote_entry_check`, so the refusal reason is `cap` as dispatched ✓.

## 14. DR-4(d) — sweep-recovery via the cap-legal rollback.sh orphan induction (C2 closure)

**First attempt — executor error, disclosed (FL-8):** ran `rollback.sh demo --to dr4d` via the proc wrapper and killed "the pid" found by `pgrep -f` — which matched the **wrapper's cmdline** (it contains the script text), not the child. SIGKILL hit the wrapper (44491); the real rollback.sh (44493) **completed normally**: `manual rollback → v0.10.6-p2.3-dr4d (re-gate green; window count 4/3)` + halt `via manual rollback (count=4)`, exit 0 (cap branch). No orphan existed. This unplanned completion is itself journaled **D-FA4.2 evidence** — the manual recovery lane executed at count=3, incremented to 4, never refused (rollback.sh:15-19 verbatim). Friction FL-8; recovery = re-induction with unambiguous `$!` pid capture.

**Second attempt — correct (target: throwaway `dr4c`, already staged, never promoted, not quarantined):**

| Step | Time (UTC) | Evidence |
|---|---|---|
| rollback.sh start (`nohup … &`, `$!`=47036 recorded to file) | 20:43:39Z | `/tmp/b6b-ev/cb-rb-pid.txt` |
| flipped:true observed → **SIGKILL 47036** (the real rollback.sh) | 20:43:46Z | kill confirmed; process DEAD ✓ |
| **Orphan proof** | 20:43:49Z | `in_flight {kind:rollback, target:dr4c, started_at:20:43:41Z, flipped:true, owner_pid:47036}`; journal `current=dr4d` (bookkeeping never ran), `previous=dr4r1`; symlink → dr4c; lock left held by dead owner (heartbeat file present — stale-break input) |
| Freshness gate (bonus) | 20:43:45Z | the killed rollback's own launcher boot: `journal sweep: in_flight rollback txn (target=dr4c) is fresh (4s ≤ 600s) — leaving alone` ✓ |
| Staleness wait | 20:44→20:53:56Z | slept 586s past started_at+600 (age at sweep: **627s**); lock heartbeat age 619s > SWEEP_LOCK_STALE_S=300 |
| Restart (stop 20:54:05Z → launcher 20:54:08Z) | 20:54:05→08Z | stop-script TERM path (transcript captured) |
| **SWEEP FIRES** | 20:54:08Z | `journal sweep: pipeline lock stale (heartbeat 619s old, owner pid 47036 dead/unverifiable) — breaking` → `STALE flipped rollback txn (age 627s, target=dr4c, owner pid 47036) — rolled back: current -> releases/v0.10.6-p2.3-dr4r1` |
| Journal writes | 20:54:10-14Z | `sweep_rollback` event; quarantine dr4c; journal current=dr4r1; count **4→5**; `halt (sweep-rollback reached cap, count=5)`; cooldown armed 21:04:11Z; NOTIFY[sweep-halt]+NOTIFY[sweep-rollback] captured in launcher.log |
| Env restored | 20:54:19Z | `/livez` 200 v0.10.5, `/readyz` ready [], `current → dr4r1`, version smoke OK, lock free |

**The literal D-FA4.2 invariant, proven end-to-end:** entry-side promotes were REFUSED at count=3 (three journaled `reason=cap` events, 20:41) — and after that, the manual recovery lane executed 3→4 (20:42) and the launcher sweep executed 4→5 (20:54), neither refused on cap, exactly "cap enforcement is entry-side only; the rollback/sweep recovery path itself never refuses on cap." Counter visible past the cap in the same 24h window that refuses entries.

## 15. Journal events verbatim — continue-batch (18 events, complete)

```text
2026-08-23T20:02:34Z | commit         | promote v0.10.6-p2.3-dr4r1 committed (gate+soak green; previous=none)
2026-08-23T20:11:23Z | commit         | promote v0.10.6-p2.3-dr4r2 committed (gate+soak green; previous=v0.10.6-p2.3-dr4r1)
2026-08-23T20:15:01Z | rollback       | auto-rollback v0.10.6-p2.3-dr4b1 → v0.10.6-p2.1-…-dr4r1 (gate fail: /readyz unreachable >120s; re-gate green)
2026-08-23T20:15:01Z | quarantine     | v0.10.6-p2.3-dr4b1 quarantined after gate failure (skipped by future promotes)
2026-08-23T20:15:01Z | rollback       | rollback complete: serving 0.10.5 on :7979; cooldown armed (600s); window count 1/3
2026-08-23T20:27:45Z | rollback       | auto-rollback v0.10.6-p2.3-dr4b2 → v0.10.6-p2.3-dr4r2 (gate fail: /readyz unreachable >120s; re-gate green)
2026-08-23T20:27:46Z | quarantine     | v0.10.6-p2.3-dr4b2 quarantined after gate failure (skipped by future promotes)
2026-08-23T20:27:46Z | rollback       | rollback complete: serving 0.10.5 on :7979; cooldown armed (600s); window count 2/3
2026-08-23T20:40:34Z | rollback       | auto-rollback v0.10.6-p2.3-dr4b3 → v0.10.6-p2.3-dr4r1 (gate fail: /readyz unreachable >120s; re-gate green)
2026-08-23T20:40:35Z | quarantine     | v0.10.6-p2.3-dr4b3 quarantined after gate failure (skipped by future promotes)
2026-08-23T20:40:35Z | halt           | rollback cap 3/24h reached (count=3) — halt-for-human; promotes refused until the window resets
2026-08-23T20:41:15Z | refusal        | HALT-FOR-HUMAN: rollback cap 3/24h reached (count=3) — … (reason=cap)
2026-08-23T20:41:17Z | refusal        | HALT-FOR-HUMAN: rollback cap 3/24h reached (count=3) — … (reason=cap)
2026-08-23T20:41:19Z | refusal        | HALT-FOR-HUMAN: rollback cap 3/24h reached (count=3) — … (reason=cap)
2026-08-23T20:42:37Z | rollback       | manual rollback → v0.10.6-p2.3-dr4d (re-gate green; window count 4/3)
2026-08-23T20:42:39Z | halt           | rollback cap 3/24h reached via manual rollback (count=4) — promotes refused until the window resets
2026-08-23T20:54:12Z | sweep_rollback | sweep: orphaned flipped rollback txn (target=v0.10.6-p2.3-dr4c, owner pid 47036, age 627s) rolled back to v0.10.6-p2.3-dr4r1; counted as auto-rollback (ADR-024)
2026-08-23T20:54:14Z | halt           | sweep-rollback reached cap 3/24h (count=5) — promotes refused until the window resets or an operator intervenes
```

*(refusal details abbreviated for width — full text in `state.json`; the dr4b1/b2/b3 rollback details carry `v0.10.6-p2.3-dr4r1`/`dr4r2` targets as shown in §13.)*

## 16. Counter trajectory + cooldown timeline

**Counter (24h sliding window):** `0 (R3.2 reset) → 0 → 0 (2 re-anchor commits, count-neutral) → 1 (dr4b1) → 2 (dr4b2) → 3 (dr4b3 — CAP ARMED, entries refused from here) → 4 (manual rollback dr4d — recovery lane, never refused) → 5 (sweep dr4c — recovery lane, never refused; sweep halt standing)`.

| Cooldown | Armed at (by) | Until | Honored |
|---|---|---|---|
| #1 | 20:15:01Z (leg1) | 20:25:01Z | slept 545s; leg2 promote 20:25:16Z |
| #2 | 20:27:45Z (leg2) | 20:37:45Z | slept 563s; leg3 promote 20:38:01Z |
| #3 | 20:40:33Z (leg3, cap branch) | 20:50:33Z | refusals ran inside it legally (cap precedes cooldown in entry order; reason=cap ✓) |
| — | manual dr4d | none | by design (`arm_cooldown=0`, rollback.sh:176) |
| #4 | 20:54:11Z (sweep) | **21:04:11Z** | **standing at close** — B6c inherits |

## 17. Live pid checkpoint table (continue-batch; read-only, zero live contact)

| Checkpoint | Moment (UTC) | pid/ppid | lstart | Diff |
|---|---|---|---|---|
| CB start | 19:56:43 | 31150/31130 | Sat Aug 22 10:04:07 2026 | identical ✓ |
| post-dr4b1 | 20:15:3x | same | same | identical ✓ |
| post-dr4b2 | 20:28:0x | same | same | identical ✓ |
| post-refusals | 20:41:27 | same | same | identical ✓ |
| **final (post-sweep)** | 20:54:31 | same | same | **identical ✓ (ps + redacted lsof byte-identical)** |

## 18. Friction log — continue-batch additions

| # | Where | Doc/ruling says | Observed | Classification |
|---|---|---|---|---|
| FL-7 | executor (r1→r2) | — | `proc_stop` TERMed the wrapper's process GROUP; `nohup` (no `setsid`) stays in-group → freshly-committed daemon tree killed (graceful 143), demo dark 2min08s; manual relaunch recovered; commit was durable. Same class as B6a FL-3 — **never proc_stop a wrapper that spawned nohup'd daemons**; let wrappers exit naturally (file-redirect, no tee) | **executor-side, disclosed** |
| FL-8 | (d) kill technique | my pre-analysis: "SIGKILL rollback.sh post-mark_flipped" | `pgrep -f` matched the proc WRAPPER's cmdline (contains the script text) → kill hit the wrapper; rollback.sh completed normally (count 3→4 via the manual lane — journaled D-FA4.2 bonus evidence); no orphan. Recovery: re-induction with `$!`-captured pid (unambiguous). **B8 runbook-fix input: the (d) induction must capture the kill target by pid at spawn time, never by `pgrep -f` of the script path** | **executor-side, disclosed** (technique, not pipeline) |
| FL-9 | relaunch portability | DR-1 shape uses plain `nohup` | macOS has no `setsid` — my hardening attempt failed with "command not found" (demo dark ~90s longer than needed); the documented plain-nohup shape works | **OK-but-noted** |
| FL-10 | exit-code capture | B6a FL-3 mitigation "background runner" | `… \| tee file` keeps the pipe fd open in the nohup'd grandchild → wrapper never exits, `PROMOTE_EXIT` line never lands. Fix used from r2 on: `> file 2>&1` redirect — wrapper exits with the script, exit code captured | **executor technique, fixed mid-batch** |
| FL-11 | retention during rollback legs | runbook: retention on commit | The NON-CAP rollback-complete path ALSO runs `retention_evict` (promote.sh:377) → legs 1/2 evicted dr4reset + dr4b1 (keep-3, pinned pair safe, quarantined evictable by design). Harmless; reset evidence preserved in the archive journal | **OK-but-noted** |
| FL-12 | ruling §6 token note | dispatch expected "NOT-READY" on f2-open | Checker renders **BLOCKED** on f2-open (§2.1 verbatim); NOT-READY is the f2-closed verdict. B8 docs must match the pack-authoritative tokens | **doc pointer for B8** |
| FL-13 | sweep residue | runbook restore: "no stray lock dirs" | The sweep's stale-break leaves `rollback.lock.d.stale.65094` in releases/ (mv-never-rmdir by design; inert — `status.sh: pipeline lock: free`, labeled as protocol artifact). Left as sweep evidence for B6c; next lock cycle or B6c's reset owns it | **OK-but-noted** |

## 19. Findings — continue-batch + final state

1. **F-B6b-6 (D-FA4.2 LITERAL PROOF):** entry-side promotes refused at count=3 (3× journaled `reason=cap`, exit 78) while BOTH recovery lanes executed past the cap in the same window — manual rollback 3→4 (20:42:37Z) and launcher sweep 4→5 (20:54:12Z, after stale-lock break). The invariant holds at every layer with journal-visible counters.
2. **F-B6b-7 (C1 closure validated):** the 2-re-anchor-commit insertion worked exactly as derived — `previous` ping-ponged dr4r1/dr4r2 across all three cap legs, never quarantined, always rollback_safe; no no-previous halt ever fired.
3. **F-B6b-8 (freshness-gate bonus):** the killed rollback's own launcher boot left the 4s-old txn alone ("fresh ≤ 600s — leaving alone"); the sweep fired only at age 627s with lock-heartbeat 619s — both gates demonstrated in one leg.
4. **F-B6b-9 (RUNBOOK-FIX POINTER for B8, per ruling):** (i) rollback legs REQUIRE a non-null `previous` — post-reset journals need 2 re-anchor commits first (F-B6b-1); (ii) under an armed cap, the (d) orphan induction is cap-legal ONLY via `rollback.sh` (D-FA4.2 recovery lane) with the kill target captured by pid at spawn (F-B6b-2 + FL-8); (iii) checker verdict tokens: f2-open ⇒ BLOCKED, f2-closed+count<N ⇒ NOT-READY (FL-12).
5. **F-B6b-10 (B6c input state):** journal `current=dr4r1` serving green (`/readyz` ready []), `previous=dr4r1` (degenerate-equal — harmless, code-commented case), count **5**, **halt standing** (sweep halt 20:54:14Z — the freshest halt carries the record), cooldown until 21:04:11Z, quarantined [dr4b1, dr4b2, dr4b3, dr4c], `in_flight:null`, lock free (+ one labeled stale-break artifact dir). `.env` bit-exact `1ba30c01…`, knob 0 lines. Evidence: `/tmp/b6b-ev/` (40+ files: per-leg stage/promote/watcher transcripts, orphan proof, sweep log excerpt, journal dumps, live checkpoints).

## 20. Verdicts (final)

`R3.2 PASS: journal reset executed on the real demo journal — exact-path assert, archive-then-fresh (md5-preserved archive kept), fresh init via dr4reset stage, counters 24h=0 / cooldown null / quarantined [] / history [] (halt CLEARED), daemon serving untouched; ledger_check.py FIRST REAL-JOURNAL run captured verbatim (exit 0, cycles 0, gate BLOCKED F2-open per §9); live untouched`

`DR-4(c) PASS: 3 fresh-version cap-exhaustion legs (dr4b1/b2/b3) on the re-anchored journal — rollback+quarantine+counter each (1→2→3), honest cooldowns, cap halt armed at #3; dr4c staged, 4th promote ×3 refused at preflight exit 78 reason=cap, 3 journaled refusals, halt standing, zero side effects, NO halt_ack; live untouched`

`DR-4(d) PASS: sweep executed past cap (4→5), D-FA4.2 proven literally — entries refused at count=3 while manual rollback (3→4) and launcher sweep (4→5, stale-lock break, sweep_rollback + sweep halt events) both executed without refusal; env restored green on dr4r1; live untouched`
