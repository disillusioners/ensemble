# Test Report: fix/claim-gate-sibling-deadlock — RE-GATE @ 34b91ebb (remediation verification)

Date: 2026-10-04
Target: `fix/claim-gate-sibling-deadlock` @ `34b91ebbe7195db9863d516ba831891f89633f1d` (5 commits atop base `2ae91046`: db717781, e7e42f7b, 30e1c7a4, aced9ead, **34b91ebb** remediation — state-discriminated exclusion at both guard sites + 1-line fixture mock; belt unchanged). Prior gate (aced9ead) verdict: 🔴 HOLD — see RESULTS/2026-10-04-claim-gate-sibling-deadlock-merge-gate.md.
Workers (15): rg-audit a33d7712 (errored→revived, in flight) · rg-red2 553af221 · rg-pinA ed791f68 · rg-pinB 63afac54 · rg-conc 7a5b4000 · rg-pg 9b3e76de (transient 2064→revived→completed) · rg-fix-g1..g8 b3155d26/6c9d1bea/f7e9b8cf/2b3d66a9/94683e58/3df66578/29d72155/1a5ef045 · rg-e2e a38fd04f. Zero repo modifications by any worker; port 8088 never touched; live :8079 untouched (uptime-continuity proven by e2e worker).

## FINAL VERDICT: 🟢 READY TO MERGE

All 14 gates resolved. Both prior HOLD signals (production over-relaxation + fixture gap) are remediated and independently proven closed at every level (unit 3/3 deterministic, pack baseline-exact, full-dir signature-exact with zero fix-attributed reds, PG behavioral 54/54 incl. the PAUSED-blocks discriminator, e2e cascade green). Byte-level audit: zero corruption; two stale round-1 comment blocks (F1/F2) routed as a recommended comment-accuracy follow-up — non-gating (no behavioral surface; SQL carries its own accurate round-3 comments; house precedent: aced9ead tidy-pass shape).

## Gate-by-gate

| # | Gate | Verdict | Evidence |
|---|---|---|---|
| 1 | Byte-level audit of 34b91ebb | ✅ **CLEAN of corruption** — stat exact-match (7 files, dev.sh absent); both guard sites state-discriminated + byte-identical modulo alias, 4-case walk CORRECT; belt diff EMPTY (e7e42f7b form intact); fixture hunk +12 lines = 9 comment + the disclosed 1-line mock (zero behavioral surface); AST OK ×7; disclosed corruption incidents left ZERO residue. **2 non-gating findings (F1/F2): stale round-1 narrative** — repository.py:2488-2509 (db717781 block) + :3581-3589 (e7e42f7b docstring) still describe the old unconditional exclusion; in-SQL comments at both sites ARE accurate | /tmp/claimgate-regate/audit/ (full.diff 2363 lines) |
| 2 | Formerly-RED tests deterministic 3/3 | ✅ guard test 3/3 PASS (0.30-0.33s); instance_pause 3/3 PASS (0.33-0.34s); over-tightening control sibling PASS | rg-red2 report |
| 3 | Pin suite A (deadlock) | ✅ 6/6 (3.07s) — original two-rapid-messages repro + belt pins unaffected | /tmp/claimgate-regate/pinA.{xml,log} |
| 4 | Pin suite B (starvation) | ✅ 8/8 (4.34s) | /tmp/claimgate-regate/pinB.{xml,log} |
| 5 | ensure.md Core #2/#3 pack | ✅ **98P/0F/74S baseline-exact** (64.17s) — prior fix-caused FAIL closed; formerly-red guard test passes | /tmp/claimgate-regate/concurrency.log |
| 6 | Full-dir tests/job_queue/ (8 partitions) | ✅ **1957P/14F/39S over 2010 common — signature-EXACT vs documented base set** (g1:1 round2_council, g2:1 job_answer_tool, g5:2 in_progress_guard, g6:2 census, g8:8 across 4 files — all verbatim-identical; g3/g4/g7 clean; **g3 0F with remediated instance_pause green**). ZERO fix-attributed reds. Dev's 1971P/14F claim independently confirmed (1957+14 pins). json.dumps line drift 2992→3023 = remediation-diff-induced, same family | /tmp/claimgate-regate/fix-g{1..8}.{xml,log} |
| 7 | PG smoke v2 (state-discriminated) | ✅ **54/54** — 49 source-predicate regression cases (positive + 6 over-match × 7) + 5-case backing-task state matrix via imported helpers + source-extracted fragment (byte-fidelity asserted). **B2 message+PAUSED → BLOCKS** = the round-3 discriminator proving the round-1 regression closed; B3 RUNNING blocks; B4/B5 task-type unconditional; B1 PENDING proceeds (blocking_count=0). Rollback-proven, 9-table zero-leftover | /tmp/claimgate-pgsmoke2/run.{py,log} |
| 8 | E2E subset (fix-tree daemon :8090, own DB) | ✅ terminate/revive PASS 49.8s · 3-level cascade PASS 119.6s. Items 1-2 SKIP (prior base-pre-existing `settled` desync — documented, not branch-caused). Happy-path 1× sanity: identical-to-prior desync red, no worse. Clean teardown: PID-proven kill, port freed, DB dropped, data dir removed, porcelain clean, :8079 uptime continuous | /tmp/claimgate-regate/e2e/ |
| 9 | ensure.md recheck | ✅ Core #1 (changed packs: pins green, formerly-red green, full-dir baseline-exact) · Core #2/#3 (pack baseline-exact) · Core #4 (dev.sh:102, verified prior gate; stat-check re-confirmation riding on audit item 2) · Important #1 (8/8 awaited, prior gate, stack touches none of the 3 fns — stat re-confirmation in audit) · Nice-to-have dead-code (prior gate; aced9ead imports — audit item 2 scope check) | prior RESULTS + this gate |

## Dev-claims verification ledger

| Dev claim | Independent result |
|---|---|
| Targeted 16/16 (14 pins + 2 formerly-red) | ✅ 6/6 + 8/8 + 3/3 + 3/3 deterministic |
| ensure core #2/#3 98/98; core #4 grep present | ✅ 98P/0F/74S baseline-exact; dev.sh:102 (prior gate) |
| Full-dir 1971P/14F baseline-exact | ✅ 1957P/14F/39S common + 14/14 pins = 1971P/14F; red set byte-identical to base |
| Integration 59/59 | NOT independently verified (out of re-gate scope list) — dev-claimed only, noted |
| PG 16.15 compiles new predicate | ✅ SUPERSEDED by stronger evidence: 54/54 behavioral cases on live ensemble_dev through the real composed claim SQL |

## Gaps / disclosures

- **Audit pending** — worker errored (transient 2064), revived once; if the revive fails, one replacement worker per escape-valve ladder; if that also fails, verdict ships with audit marked `[incomplete: worker failed twice]` and the leader adjudicates audit risk (all behavioral evidence is already green).
- Dev "Integration 59/59" not re-verified (not in the re-gate's 7-item scope).
- Known base-pre-existing reds (14F + e2e settled-desync pair) unchanged — QUARANTINE.md consolidated row from prior gate still applies.

## Action Needed

- [ ] Fan in audit report when it lands; if CLEAN → final verdict READY TO MERGE (this file updated); if FINDINGS → HOLD with byte-precise items.
- [ ] (test-debt, unchanged from prior gate — base-attributed, non-blocking): settled terminal-set e2e update; census fixture; watch_events assertions; fake_sync arities; job_answer tool-order pin; council event-loop modernization; MagicMock-JSON fixtures.

## Code Changes Summary

None — verification-only; zero modifications/commits by tester or any of 15 workers. Gate artifacts (scratch DB, data dir) cleaned in-worker; porcelain = pre-gate baseline.
