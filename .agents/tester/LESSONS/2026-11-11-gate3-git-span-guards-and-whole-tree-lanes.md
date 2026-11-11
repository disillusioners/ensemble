# Gate3 lessons — git-span guards on feature tips, whole-tree leg serialization, post-restart worker recovery

**Context:** skill-system-revival Phase 3 functional gate (2026-11-10/11), branch `feature/skill-capture-prod-wiring` @ `4159d4670`, base `0efdf106f`. Full record: `RESULTS/2026-11-11-skill-revival-phase3-functional-gate.md`.

## 1. `TestP11BootNoBehaviorChange` git-span guards are RED on ANY feature tip that touches `daemon/manager.py`

Expect exactly these 2 net-new ids in any whole-tree base-vs-head diff for such branches (deterministic, both interpreters):
- `test_the_decision_lane_manager_drift_is_confined_to_decision_hunks`
- `test_the_memoir_lane_manager_drift_is_confined_to_sanctioned_lane_hunks`

Mechanism: the guard diffs `daemon/manager.py` over the span `(rev-list --first-parent --merges -1 HEAD)..HEAD`. On a feature tip the span contains the branch's own un-landed hunks → red unless hunks carry lane markers. On `latest` the span is empty (last first-parent merge IS recent history) → vacuous green.

**Resolution recipe (4-arm, ~15 min, throwaway `--local` clones, never push):** (A) pinned-base `--no-ff` merge → guards must PASS; (B) feature tip → must FAIL (discriminating control); (C) current `origin/latest` `--no-ff` merge → must PASS; (D) squash counter-case → must FAIL.

**Landing requirement:** the landing MUST be a `git merge --no-ff` (a squash/rebase landing keeps both guards RED — span falls back to the older merge and the drift re-enters). Post-landing proof pair: `git rev-list --first-parent --merges -1 HEAD` == HEAD **and** empty `git diff <that>..HEAD -- daemon/manager.py`, then guards green. Note: merged-shape green is span-empty-vacuous by design.

## 2. Serialize whole-tree Gate A legs — concurrency injects ~90 flake ids

Base and head legs overlapping on one 16-CPU host produced ~90 partition-flake delta ids (session-state swap families, concurrency cells) that required 3× isolated serial adjudication (+6× stress on borderline) to clear — ~1h of pure adjudication cost. Run legs strictly serially per host, or on separate hosts. (An unrelated lane's pytest running concurrently counts too.)

## 3. Digest hygiene for failed-list comparisons

- Compare **within-form only** (BARE-ID vs BARE-ID, GREP-FULL vs GREP-FULL); normalize the ` - reason` suffix before `comm` — a form mismatch between workers produced 9-11 phantom ids.
- BARE-ID anchor on `^(FAILED|ERROR) tests/`; raw grep also catches daemon LOG lines starting `ERROR ` (57-59 per leg here).
- Whitespace-parametrized test ids get split by `sort`/`comm` — re-run suspect ids directly before believing the delta (4 such artifacts, all passing).

## 4. Post-daemon-restart "stuck RUNNING" workers: monitor before terminating; NEVER pause

After a daemon restart, dispatched workers parked on lost tool-result pairings showed status RUNNING with zero turns for hours — yet **self-recovered via checkpoint resume and delivered complete evidence** (both lanes did). Watch `/tmp` artifact mtimes and child pytest processes before declaring death.

**`pause_instance` on such instances correlated with daemon crashes 2-for-2** (anomaly `ed3f806a`, major) — each attempt took the self-system down and returned `[Tool execution interrupted (daemon restart/crash)]`. Recovery route that worked: leave them alone, or route a fresh salvage worker at the disk artifacts.

## 5. Shard-killer cell

`tests/unit/test_skill_sink_class_containment_pins.py` has a ~241s module-scoped setup (drives members in subprocesses): any ≤3300-node shard containing it hits the 300s cap (produced 124s at base s14 and head s10/s10a). Pre-split it into its own leg with a documented 600s cap; all 4 nodes PASS isolated.

## 6. Dispatch-shape guardrail (tester lane)

A worker composing its own background orchestration (`proc_run` + wait) wedged for 1h54m inside one unbounded bash call (anomaly `4d45310d`). Dispatch contracts for long lanes must mandate: foreground only, one shard per bash tool call, explicit per-call tool timeout, inner `timeout 300` — and crash-resumable on-disk manifests so any restart is recoverable by a salvage worker.
