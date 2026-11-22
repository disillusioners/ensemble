# Engine Arc FLIP-PREP — Tester Gate Report (the kill-switch execution wiring; the trust-transfer legs the review council could not boot)

- **Date:** 2026-11-22 · **Tester role:** dispatch-only gate
- **Tree:** `/home/nea/ensemble-src-wt-engine-flipprep` · branch `feature/engine-killswitch-exec`
- **Tips:** all legs @ `72d64e867b1eec3806810f47ac3381d4c09af4e4` (the 4-commit lane: `688042646` D3.6/D3.12 wiring → `ad0175229` F1 posture truth → `290000cbd` F2 gate ownership → `72d64e867` W1–W5 riders); the registration fold landed post-gate as `7b97bee7d`. TRUE base `8a1045c02` (v0.31.32 bump).
- **Evidence:** `raw/2026-11-22-engine-flipprep-tester-gate/` (25 artifacts: 4 boot logs, warm-gate transcripts ×2, 12 ceremony legs, Tier-2 digests/ladder/attribution)

---

## VERDICT — GATE PASS. NOTHING BLOCKS THE MERGE.

| Leg | Verdict |
|---|---|
| 1 — the kill-switch boot leg (4 boots) | ✅ **PASS** (a)–(e), with the instrument disclosure |
| 2 — the warm gate | ✅ **PASS** all-4 legs; GATED **149.218 ms** (inside the band) |
| 3 — ceremony (12 legs) + M1-M3 | ✅ **12/12 green**; M1-M3 **PROVEN-WITH-GAPS** at the production seam |
| 4 — Tier-2 regression | ✅ **NET-NEW = ZERO** (adjudicated) |
| 5 — this verdict | PASS |

---

## 1. The kill-switch boot leg (ports 8091-8094, disposable PG each; boot-scoped instrument — disclosure below)

**INSTRUMENT DISCLOSURE (verbatim):** "runtime trip NOT exercisable on a running daemon (no surface — the resolver reads live `os.environ` every tick via the injected `os.environ.get` at `api.py:3870`/`killswitch.py:78`/:102/`execution.py:315`, but ZERO env-writers exist: no route, settings write, signal, or file feeds it — the in-test next-tick proofs use an injected dict no production wiring provides); boot-scoped restart instrument exercised; **DB-tier switch = the routed D-7 follow-up**."

| Boot | Shape | Core evidence |
|---|---|---|
| **B1** `5b056dd0` | flag on + shadow off (active) | **5 × SUCCEEDED** orphan runs at 90s cadence; SUPPRESSED + executor-registered lines; posture `enabled=True shadow=False … use_engine_flags=SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=true (1/12)` (F1 TRUTHFUL — the pilot-gate's flag-blind finding is FIXED by `ad0175229`); 0 KILL_SWITCHED anywhere; 0 FAIL-TO-LEGACY |
| **B2** `04e97932` | + `ENSEMBLE_ENGINE_ENABLED=false` (engine-wide trip AT BOOT) | **ALL 12 jobs stamp `SKIPPED_KILL_SWITCHED`/`KILL_SWITCHED`** — the mass-rollback lever in one query; **0 SUCCEEDED** (executor never called); orphan `last_skip_reason=KILL_SWITCHED`; posture `enabled=False`; **legacy OWNS at boot-trip** (started line present — boot-scoped trip = SAFE fail-to-legacy; the "nobody-owns" gap is RUNTIME-trip-only, per killswitch.py's W4 scope note — empirically corrected, not a defect) |
| **B3** `5c028648` (carried DB from B2) | engine-wide unset (AMD-8 absent ⇒ enabled) | **Execution RESUMES**: 2 × SUCCEEDED; **restore gap ≈ +1.3s from readyz-green** at the next due window (90s after B2's last skip — the W1 interval-bound shape, empirically confirmed); 0 KILL_SWITCHED in B3 |
| **B4** `f08749b9` (fresh DB) | flag = `garbage` (per-job malformed) | **ONLY the orphan job** stamps `SKIPPED_KILL_SWITCHED` ×3 (0 SUCCEEDED); **the 11 observers continue `SKIPPED_SHADOW`** — per-job isolation proven; posture renders `use_engine_flags=…=garbage (1/12)` truthfully; legacy owns (garbage ⇒ not-trusty ⇒ safe fallback) |

Boot SLOs: 19/18/13/19 s (host-frame, ≤120s). All teardowns verified (ports freed, PG stopped, scratch removed). Transcripts: `raw/…/flipgate_b{1..4}_42_daemon.txt`.

**skips_by_reason counting:** proven at the SQL surface (the runs/jobs columns above); the in-RAM counter + the read-model projection are pinned by the M1/M2 in-test legs and the observability pack.

## 2. The warm gate (leg 2)

`RESULT: PASS` — **all four legs** at the committed tip, independently executed (trust transferred: the council's boot failure was NOT a pack defect). LEG 1 21/21; LEG 2 doc-pin (250/360) green; **LEG 3 executable WARM boot: GATED_MS = 149.218 ms ≤ 250** with all four posture fields asserted (`use_engine_flags=` now among them); LEG 4 COLD disclosure. **The datum is INSIDE the committed band 103.310–191.188 — NO band-table bump.** (The ceremony re-run measured 198.310 ms on a contended host — loadavg 22–23/16 cores, the gate's own parallel fan — +7.1 ms over the ceiling but 51.7 ms under the bound; recorded as a contended-host observation, NOT a band row, to keep the band's clean-host semantics. The in-tree staleness rule remains ABSENT — preflight finding, untriggered by the designated measurement leg.)

## 3. Ceremony (REAL clones @ `72d64e867`) — 12/12 green

substrate **145/145** + AC10 411 · execution **40/40** (pre-fold; now 51) · guardrails **40/40** · boot **26/26** (pre-fold; now 35) · slo_gate **all-4 PASS** (LEG-3 contended datum above) · observability **93/93** (confinement pin GREEN — the flip-prep's 5 daemon paths contained by the post-addendum literals) · event_registry **69/69** · dormant_posture **3/3** · diff_audit **2/2** (containment **12 ⊆ 54, zero offenders** — no re-anchor needed) · pilot parity **59/59** (full parity table) · census **22/22** (30=23/6/1 assert-proven).

**M1-M3 re-derivation (disposable extracts, live worktree never mutated):** baseline 11/11 green; **V1 gate-removed → 5 RED** (the per-job/engine-wide trip legs + both unreadable-polarity legs + the real-tick-loop re-arm leg); **V3 counter-removed → 2 RED** (KeyError on the counted reason — both trip legs); **sibling control green** (comment-only edit, no false-RED). **V2 (absent⇒enabled split inverted) → 0 RED in the exec-wiring file — a COVERAGE GAP at that seam** (every exec-wiring test sets the key explicitly; the split is guarded separately by `test_engine_kill_switch_dualpath.py::test_absent_engine_defaults_true`). VERDICT: **PROVEN-WITH-GAPS** — the gaps disclosed, not faked.

## 4. Tier-2 whole-tree regression (core seam `daemon/api.py` ⇒ mechanical Tier 2)

BASE `8a1045c02` (40,682 ids) vs HEAD `72d64e867` (40,702; **head-only = exactly the 20 new pins, 20/20 green**); symmetric id sets, 4+1 shards/leg, accounting closure EXACT; fence = **89 per-variable scrubs** incl. the derived 33-name AUTH union, 0 sensitive survivors; LLM fence.
- Two-form digests: GREP-FULL 20 base-only / 34 head-only; BARE-ID 20/33 (the gap = daemon LOG lines — the form-tag trap). Comparator non-vacuous.
- Symmetric re-run collapsed the 53-id union to 4 real diffs; **3× isolated ladder — 318 single-id invocations, 0 gaps**: 46 composition artifacts (3/3 PASS both legs), 5 pre-existing (red both sides), 1 fixed-at-head, **1 candidate → adjudicated FLAKE** (`test_dependency_bus.py::test_per_parent_lock_serializes_db_insert` — InvalidRequestError refresh-race, IDENTICAL to QUARANTINE row 82 (2026-08-29 StaticPool fixture-infra); BASE 3/3 PASS vs HEAD 1F/2P = non-deterministic; **empty lane diff on the file AND on all dependency-bus production code**; the node also flipped base-only in the sweep — flake in both directions).
- **NET-NEW = ZERO.** Zero reds in ANY lane-owned file on either leg.
- Projection: probe-derived breach (+36%) → ONE sanctioned re-scope (parallelism to the measured `-n 16` optimum, file-boundary 4-shard split; scope unchanged) → shard-0 checkpoint CONFIRMED the rate → breach SURVIVED → **actual ≈115 min ≈ 28% over the 90-min advisory, reported loudly** (host contended 15–35 load; a sibling emulator held ~9 cores).
- Harness defect found + corrected mid-lane (disclosed): the first ladder dispatcher word-split space-bearing ids (`xargs -n 5`) and truncated log names — rebuilt (one quoted arg per id + sha1-prefixed names), all 11 affected ids re-run; the mis-dispatched source excluded from classification.

## 5. Registration closure (preflight finding #3 — CLOSED at the gate)

The lane's 20 unpacked nodes are now registered: `7b97bee7d` folds `test_kill_switch_exec_wiring.py` (11) into the **execution** pack (40→**51**, PASS 51/51) and `test_boot_posture_truth.py` (9) into the **boot** pack (26→**35**, PASS 35/35). PACKS rows + the counts line updated to current truth (incl. the slo_gate row's F2 4-leg body refresh and the 65→69 event_registry re-baseline).

## 6. Findings (routed per the mission: fixes → dev, findings → leader)

| # | Finding | Route |
|---|---|---|
| F-1 | **No runtime trip surface** — the kill-switch is env-only; a running daemon cannot be tripped by any provided instrument (zero env-writers; "next tick, no restart" holds only for a mutating source that does not exist in production). Boot-scoped restart is the only instrument. **DB-tier switch = the D-7 follow-up** (already routed). | leader (design posture; D-7 carries the remedy) |
| F-2 | **V2 coverage gap at the exec-wiring seam** — the absent⇒enabled (AMD-8) split is not pinned by `test_kill_switch_exec_wiring.py` (guarded only at the killswitch layer by the dualpath test). One added test node (absent key at the wiring seam) closes it. | dev (small test addition) |
| F-3 | **`engine_execution_unit_test.sh` in-script AUTH-family scrub gap** — the pack's own SCRUB array lacks the 7 `ENSEMBLE_AUTH_*` vars (neutralized at invocation level in every gate run; a bare future invocation risks collection ERRORs masking reds). | dev (one-line fence completion) |
| F-4 | **In-tree staleness rule absent** for the WARM band (no "bump if >191.188" rule committed; LEG 2 pins only the 250/360 constants). Untriggered this gate (datum in-band). | leader (author the rule or accept absent-and-disclosed) |
| F-5 | **Contended-host datum 198.310 ms** (ceremony re-run, loadavg 22–23) — first number above the band ceiling; under the bound. Recorded as an observation, not a band row. | leader (flip-day observability: the ACTIVE-shape block on a loaded host can exceed the dormant band) |

## 7. Bottom line

The flip-prep lane's four commits are **fully verified on real boots, real clones, and the whole tree**: the kill-switch execution wiring trips and restores exactly as designed (boot-scoped), the F1 posture line is now truthful (the pilot-gate's flag-blind finding fixed), the F2 gate measures its own number (149.218 in-band), all 12 ceremony legs are green, M1-M3 discriminate at the production seam (with two disclosed coverage nuances), and the regression is net-new ZERO. **No merge blockers.**
