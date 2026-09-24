# Final Live Gate Report: DEFECT-1 closure — fix/watch-notify-delivery-gaps @ 81fc769d
Date: 2026-09-24 (session 07:22Z–07:5xZ)
Stack: 5f4e35b0 → 1e12944a → 89e4b397 → 439ec3e1 → 0d75e068 → ef5000e4 → a29b3309 → **5292eb99** (winner-site fix) → **81fc769d** (M3 narrowing)
Method: same rails — real dev daemon (ensemble_dev, LOG_LEVEL=debug), real Ari (fa63cf0c), real LLM; byte-level Ari-vs-DB reconciliation.

## FINAL VERDICT: **GATE PASSABLE — DEFECT-1 CLOSED LIVE. ALL 4 ITEMS PASS.**

| Item | Verdict | Key evidence |
|---|---|---|
| 1. DEFECT-1 closure (4/4 multiplicity) | ✅ **PASS** | W1-W4 (2a7063c5, 79310b5c, b58ed22e, a3380312): every terminal body **223 bytes WITH `Result:` + markers FINALW1-4**, byte-matched to Task.result; 56-57B failure shape dead on all legs |
| 2. M3 non-regression (corrected adjudication) | ✅ **PASS** | Settled mirror b594dce4: **55-byte lean body** (`settled ✓`, no Result:) — fan-out narrowing proof; zero structural breakage (no wrong-slot/error-leak/duplicates) |
| 3. Invariant probe | ✅ **PASS** | Delta-arm verbatim (`Armed 0 live receipt(s), 3 already-settled receipt(s) skipped — no historical replay`); flip-probe → **0 new [JOB_EVENT]s** |
| 4. ⟳ non-regression | ✅ **PASS** | 2 independent coder cycles (c710d098, 618aef4a): ⟳ 82B with **CHECKG1** before ✓ each; watcher survived ⟳; terminal exactly-once |

## Item 1 detail (the closure proof)
4/4 task-kind completed envelopes at 223 bytes (vs 56-57B failure baseline at ef5000e4), markers FINALW1/W2/W3/W4 present in every body and matching Task.result content. The winner-site fix (5292eb99: ChildReportsService._dispatch_post_commit_side_effects fan-out threads result_summary=last_content) demonstrably lands on the path that delivers.

## Instrumentation finding (NEW, filed — diagnostic tooling, not product)
The round-3 `[watch-cas]`/`[watch-deliver]` instrumentation **never emitted** during the entire live run (0 lines for any claim; gated by `logger.isEnabledFor(DEBUG)` at work_notifier.py:539/628; uvicorn ran `--log-level debug` but the work_notifier logger does not propagate to the active handlers). Attribution in item 2 was therefore inferred from body shape (permitted by the corrected adjudication). The closure evidence stands on the delivered bodies themselves; the instrumentation's logger-propagation gap needs its own fix before it can serve future arbitration.

## Mechanical (final HEAD 81fc769d)
| Pack | Result | Adjudication |
|---|---|---|
| Pins (round3 ×4 + defect1 ×7 + defect5 ×6; per commit contract, defect1b ×3 excluded) | ✅ **17/17** (4.96s) | incl. measured-winner race pin, late-loser non-suppression, M3 settled pin, AST call-site guard |
| Full tests/job_queue/ | 🟢 13F/1,879P/39S (64.5s) | EXACT baseline set; round3 pin file green inside sweep; wedge flake in documented envelope; +20 collected = branch pins |
| Full tests/unit/services/ | 🟢 13F/1,900P (42s) | exact 13/13 baseline match, zero new |
| Full tests/unit/tools/ | ✅ 2,841P/0F (56s) | all green; +1 net-new archive test ran+passed |

## Low-severity notes (from live round)
- Continuation arm-fast races fast workers (first SETTLEDG settled before arm; sleep-30 retry pattern works) — operational note.
- Settled 55B bodies now overlap the old DEFECT-1 55-57B size heuristic — future automated checks should discriminate by STATUS token, not body size.
- Ari auto-recovery produced one extra coder job (89770002) pre-prompt — harmless noise, noted.

## Safety ledger
Ambient-LIVE POSTGRES scrubbed every lane (verified pre-run each time); engine ensemble_dev; 9797/7979/8088 untouched (unrelated parallel demo-promote activity observed and avoided); .env backup/restore discipline incl. LOG_LEVEL=debug for the round (restored at teardown); no fixes; parked refs untouched; evidence logs in /tmp.

## Teardown
Dev daemon graceful on single TERM to uvicorn 1103378 (full tree cascade; all 5 workers drained claimed=22/completed=22/failed=0; "Graceful shutdown complete"); 8079 freed. LIVE 9797 (819745) + demo 7979 (800677) unchanged (elapsed-time progression confirms alive, untouched). `.env` restored byte-identical (sha256 11db2814…; LOG_LEVEL=info + mock combo back). Git: zero tracked modifications; branch fix/watch-notify-delivery-gaps @ 81fc769d intact. Evidence preserved (/tmp/e2eg-* incl. 11.8MB daemon log, pre/post baseline snapshots).
