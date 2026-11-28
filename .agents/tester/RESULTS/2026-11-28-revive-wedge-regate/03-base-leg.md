# BASE LEG — Direction-1 pin RED at the pre-fix base (leg 03)

Date: 2026-11-28
Base commit: `5cc5912f706fda7cc2793973aaf8521aceec19d1` (origin/latest, the narrow two-arm guard ABSENT)
Base worktree: `/home/nea/ensemble-src-wt-revive-gate-base` (detached; the ONE extra worktree the commission authorizes)
Pin under test: `tests/unit/services/test_child_reports.py::TestReviveWedgeDirection1Canonical::test_revive_then_new_anchor_terminalizes_and_persists_report_injection`
Worker: `fb1b7bc4-4a25-425e-9400-aefc58f6841a`

## VERDICT: **RED — genuine assertion failure** (exit code 1)

## 1. Pre-flight (verbatim)

```
$ cd /home/nea/ensemble-src-wt-revive-gate-base && git rev-parse HEAD
5cc5912f706fda7cc2793973aaf8521aceec19d1

$ git rev-parse --abbrev-ref HEAD
HEAD                                    ← detached, as expected

$ git status --porcelain
                                       ← EMPTY (clean)

$ sha256sum /home/nea/ensemble-src-wt-revive-wedge/tests/unit/services/test_child_reports.py
8cdf6af619e01ef26bf8f8e122b44dde1230d8864a7c5416c5fd6d4976db7632
```

Overlay source sha256 **MATCHED** the expected `8cdf6af6…` — the bytes did not move during the leg.

## 2. Overlay fidelity (sha256 AND blob level)

```
$ sha256sum /home/nea/ensemble-src-wt-revive-gate-base/tests/unit/services/test_child_reports.py
8cdf6af619e01ef26bf8f8e122b44dde1230d8864a7c5416c5fd6d4976db7632
```
Both sides `8cdf6af6…`. Blob-level: overlay blob `7ce534a728825b47321845c3ffabc0f4b3edc3fa`
≠ the base tree's own committed blob `a27c4d4d77901c05b658b4dfcc408fed68d81a9c`
⇒ the `cp` was **not** a no-op; the base tree really carried the tip test bytes during the leg.

## 3. Import resolution — the vacuity guard (verbatim)

The tip venv is an editable path-append install whose `.pth` injects the TIP tree; resolution is
CWD-driven. `-P` / `PYTHONSAFEPATH=1` was NOT used (it would silently resolve `daemon` to the tip
tree and report a false green).

```
$ cd /home/nea/ensemble-src-wt-revive-gate-base && PYTHONPATH=/home/nea/ensemble-src-wt-revive-gate-base /home/nea/ensemble-src-wt-revive-wedge/.venv/bin/python -c "import daemon, daemon.services.child_reports as cr; print('daemon_file=', daemon.__file__); print('cr_file=', cr.__file__)"
daemon_file= /home/nea/ensemble-src-wt-revive-gate-base/daemon/__init__.py
cr_file= /home/nea/ensemble-src-wt-revive-gate-base/daemon/services/child_reports.py
```

In-session probe (`-p probe_resolve`, `/tmp/probe_resolve.py`, `PYTHONPATH=/tmp:<base>`) — proving the
resolution **held during pytest**, not merely in a separate interpreter:

```
[PROBE] daemon.__file__ = /home/nea/ensemble-src-wt-revive-gate-base/daemon/__init__.py
[PROBE] daemon.__path__ = ['/home/nea/ensemble-src-wt-revive-gate-base/daemon']
[PROBE] child_reports.__file__ = /home/nea/ensemble-src-wt-revive-gate-base/daemon/services/child_reports.py
[PROBE] sys.path[0:5] = ['/home/nea/ensemble-src-wt-revive-gate-base', '/tmp', '/home/nea/ensemble-src-wt-revive-gate-base', ...]
```

## 4. The Direction-1 node at base — the RED

Exit code **1**. Classification: **ASSERTION failure** (a genuine red) — NOT a collection/import error.
`1 failed, 1 warning in 1.96s`.

Verbatim failing assertion:

```
tests/unit/services/test_child_reports.py:3273: in test_revive_then_new_anchor_terminalizes_and_persists_report_injection
    assert not any(
E   AssertionError: Direction-1: the idempotency_skip log line must NOT be emitted for a revived child completing under a new anchor
E   assert not True
E    +  where True = any(<generator object TestReviveWedgeDirection1Canonical.test_revive_then_new_anchor_terminalizes_and_persists_report_injection.<locals>.<genexpr> at 0x73eafe7d11c0>)
```

Verbatim captured log (the canonical base signature):

```
INFO     daemon.services.child_reports:child_reports.py:3683 Instance rw-child... parent_id=rw-leader, status=running
INFO     daemon.services.child_reports:child_reports.py:4881 Instance rw-child... completed, sending report to parent rw-leade...
INFO     daemon.services.child_reports:child_reports.py:5607 Bus-active: skipping inline cascade for parent rw-leade... — no inline status write; ...
INFO     daemon.services.child_reports:child_reports.py:3683 Instance rw-child... parent_id=rw-leader, status=running
INFO     daemon.services.child_reports:child_reports.py:4699 Instance rw-child... completion report skipped: reason=idempotency_skip
```

The `status=running` line precedes the skip ⇒ **the instance row stays `running`** (the wedge) and the
report is suppressed. Base mechanism confirmed statically at `child_reports.py:4676-4705`: WIDE
single-arm prefix guard, status set `[READY, PROCESSING, COMPLETED]`, unconditional early return
**before** the CAS.

## 5. Full overlaid file at base (informative)

**66 collected · 51 passed · 15 failed · 0 deselected · 0 skipped · no collection errors.**

FAILED node ids verbatim (from the pytest *short test summary info* section only — no whole-output grep,
so no phantom reds):

```
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child[completed-anchor-A-anchor-A-i]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child[processing-anchor-A-anchor-A-ii]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child[ready-anchor-A-anchor-B-iv-ready]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child[processing-anchor-A-anchor-B-iv-processing]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_the_suppress_outcome_reaches_the_b4_gate_on_a_completed_row
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDuplicateFreedom::test_an_explicit_revive_then_a_new_anchor_mints_a_second_report
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_arm_b_suppression_emits_exactly_one_record_and_counts_only_a_loss[different-anchor-is-a-loss]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_arm_b_suppression_emits_exactly_one_record_and_counts_only_a_loss[same-anchor-loses-nothing]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_an_arm_a_only_suppression_emits_exactly_one_arm_a_record
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_a_mint_over_a_retrying_obligation_emits_exactly_one_info_record
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_no_retrying_record_without_an_obligation_or_on_a_suppression
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_concurrent_counter_increments_lose_no_update
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_a_defer_emits_no_suppress_record_and_leaves_the_counter_untouched[active_children]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_a_defer_emits_no_suppress_record_and_leaves_the_counter_untouched[pending_task]
FAILED tests/unit/services/test_child_reports.py::TestReviveWedgeDirection1Canonical::test_revive_then_new_anchor_terminalizes_and_persists_report_injection
```

The Direction-1 pin **plus the lane's other new pins** are red at base — normal and informative, **not** a
gate failure: it is exactly what a pre-fix base should do to a post-fix test file.

## 6. Restore proof

```
$ cd /home/nea/ensemble-src-wt-revive-gate-base && git checkout -- tests/unit/services/test_child_reports.py
$ git status --porcelain
                                       ← EMPTY ✅
$ git rev-parse HEAD → 5cc5912f706fda7cc2793973aaf8521aceec19d1   (detached)
```

Post-restore file sha `c4a934cd71b241b8c986832f0f21f22bbd823abc64c38402ed95bb0ce1c09db3` = the base
version, not the overlay. Worktree **left in place** (a later cleanup leg removes it).

## 7. Raw evidence files

| File | Contents |
|---|---|
| `base-direction1-node.out` | STEP 4 full output + exit code + classification (4106 B) |
| `base-full-file.out` | STEP 5 both runs + exit codes + counts + FAILED ids (7538 B) |
| `base-probe-and-fence.txt` | STEP 1 + 2 + 3 + 6 verbatim: HEAD, detached, porcelain, both sha256 values, resolution lines, blob hashes, base mechanism excerpt (6546 B) |

## 8. Corroboration — an independent scratch-extract proof

The Direction-1 author (worker `1b31e057`) had already reached the same result independently, by
`git archive 5cc5912f7` into `/tmp/rw_vacuum` (a read-only extract — NOT a worktree) with the test file
overlaid and `PYTHONPATH` pinned: full node at base **RC=1** on the same (a) assertion, with
`child_reports.py:4699 … completion report skipped: reason=idempotency_skip` captured, and the isolated
(d) check failing with `report_injections rows: 1` (the revived lifecycle mints none). Two independent
workers, two independent trees, same RED.

## 9. Leg-scoped notes

- A concurrent sibling leg (the M1/M2 mutation leg) was editing `daemon/services/child_reports.py` in the
  **tip** worktree during this leg (mtime `10:14:10`, diff `+ MessageStatus.COMPLETED.value` at
  `child_reports.py:141`). That is the mutation leg doing its job; it cannot affect the base leg, whose
  resolution was proven into the **base** tree in-session. The tip **test** file sha256 was re-verified
  unchanged (`8cdf6af6…`) after the leg.
- This leg proves the first conjunct of a two-tree RED proof (node ∈ BASE failed set). The second
  conjunct (node ∉ HEAD failed set) is carried by the tip-side pack runs: the node passes at tip
  (1/1) and the whole file passes (66/66) — see `01-pack-results.md` and `02-direction-pins.md`.
- `snapshot_create` was refused twice (the worker could not self-identify its instance id); optional
  here — the durable state (clean base worktree at `5cc5912f7`, evidence paths) is discoverable via
  `git worktree list` and the RESULTS dir.
