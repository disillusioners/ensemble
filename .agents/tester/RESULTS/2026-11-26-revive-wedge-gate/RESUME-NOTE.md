# RESUME-NOTE — revive-wedge gate (replacement tester) — STATE @ 2026-11-26T16:00Z

Lane `fix/revive-wedge-terminalization` @ `/home/nea/ensemble-src-wt-revive-wedge`, HEAD `9fc39c056`, base `35ec50e3c`.
Replacement for dead tester 968f4659. Evidence root: `.agents/tester/RESULTS/2026-11-26-revive-wedge-gate/`.

## DONE (evidence on disk)
| Leg | Result | Evidence |
|---|---|---|
| STEP 0 hygiene | CLEAN — no live mutation; nothing restored | `00-step0-hygiene.txt` |
| Predecessor audit | NO evidence on disk ⇒ every leg newly run; predecessor's `/tmp/rw-mut-L2b2` carried a LIVE b2 mutation (`de0e6af5…`≠HEAD) — recorded then REMOVED | this note |
| L0 static/import + tool-name drift | PASS (imports ok, 4 system-log names unchanged, drift pin 7 passed) | `10-L0-static-import.txt` |
| L1 core | base 30 failed/102 passed → merge 132 passed/0 failed; digest cmp rc=1; **comm -13 net-new = 0**; 30 base-only reds all FIXED at merge; **zero collection deltas** (13/22/65/13/17/2 both sides) | `20-L1-console.txt` |
| L2N stay-green net | base 85 passed / head 85 passed, PASSED-set byte-identical (cmp rc=0), 0 reds both sides | `20-L1-console.txt` |
| L1 3× re-runs | **60/60 node-side groups identical RC over 3 runs; 30 BASE all-RED 3/3; 30 HEAD all-GREEN 3/3; 0 unstable** | `21-L1-reruns.log` |
| L8 base leg (Component C) | **3 failed / 8 passed — exactly PC-1, PC-2, PC-3(a); no collection error** ⇒ genuine RED | `20-L1-console.txt` |
| L8 merge leg | 11 passed (11-node census confirmed) | `20-L1-console.txt` |
| L4 real-PG | base **1 failed** (asserted: `outcome == "idempotency_skip"`, not `regular_child_completed`) → merge **1 passed**; **0 skipped**, 0 errors, node id present | `37-L4-base.out` `38-L4-merge.out` `40-L4-nodeid.txt` |
| L4 stay-GREEN PG nets | 5 passed + 4 passed on BOTH base and merge | `39-L4-net*.out` |
| L7 registration | 3 blocks present; all 16 files have rows; Component-C block **byte-identical** to gate-matrix §11.5 (cmp at lines 540-554); census claims (65/13/13/22/11/17) all match my measurement | `30-L7-registration.txt` |
| Mutation driver | validated on L2a (PASS) | `22-mutation-matrix.jsonl` |

## IN FLIGHT
- `04-mutation-driver.py` — 29 legs (L2a/L2b/L2b2/L2c/L2d, M-C1..M-C8, NEW-7 a-e, B-M1..B-M10) → `22-mutation-matrix.md` + `mut-<leg>.out` per leg.
- `05-remaining.sh` — L3, L5 (merge+base), L6 (101 cases), fence proofs, ORIGINAL-SYMPTOM CLOSURE → `31-remaining-console.txt`.

## REMAINING AFTER THAT
- `05b-regression-packs.sh` (SOLO): child_reports_unit_test, child_parent_lifecycle_regression_test, regression_unit_tools_test (dir-glob, 5908 nodes).
- Final assertions (lane sha256 parity + porcelain) + worktree teardown + final report.

## FINDINGS TO CARRY INTO THE REPORT
1. **L5 base expectation NOT met** — gate-matrix §6 says the drive-outcome pin is RED at base; measured GREEN at base (0 failures from `test_stale_running_sweep_pins.py` in base.out). The pin's fixture (`_seed_shape_b`) does not carry the delivered-prior-report precondition, so the L5 pin does not discriminate base vs merge. `_patch_drive_seams` stubs only assistant-history seams (NOT the mint guard) ⇒ the pin is not hollow, it is non-discriminating.
2. **L4 credential deviation** — the lane .env app role `ensemble_dev_app` has NO CREATE on `ensemble_test` / schema `public` ⇒ every `pg_engine` setup ERRORED (`no schema has been selected to create in`). The lane used the conftest's own defaults (`ensemble`/`ensemble_dev`), which is what every repo PG pack uses. Not a skip; an error; fixed by the credential swap.
3. **M11 does not exist** — the commission says "B's M1–M11"; observability.md defines M1–M10 only. I ran 10/10 and flag the discrepancy.
4. Predecessor left 3 registered scratch worktrees + 1 unregistered dir — all removed (recorded shas above).
