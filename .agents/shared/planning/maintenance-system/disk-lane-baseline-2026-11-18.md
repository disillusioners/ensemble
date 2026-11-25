# Disk Lane Baseline — 2026-11-18 (giter, disk cleanup rounds 1+2)

PM ledger: ENSEMBLE-35 (Central maintenance system) · Board tag: `maintenance-system`
Purpose: first measured baseline for the disk lane. Round 1 (2026-11-18 ~04:00–08:40Z, leader+sweep): ~69 GB reclaimed, 80 worktrees swept. Round 2 (this document, 14:27Z+): /tmp triage + 13 worktrees + journal .bak hygiene.

## Round-2 headline numbers
- BEFORE: root fs 230G total · 162G used · 69G free (71%) · inodes 6,238,157/31,129,600 (21%). /tmp ≈ 86.6 GB / 3.20M entries (~51% of ALL used inodes).
- AFTER (14:55Z): 102G used · 128G free (45%, was 71%) · inodes 3,919,260/31,129,600 (13%, was 21%). Round-2 freed: ~60 GB bytes (53.9 GB from /tmp: 86.6→32.7 GB) + ~2.32M inodes (6.24M→3.92M). /tmp residual 32.7 GB = mostly <48h-active lane trees (age out next round), skillfix incident family, live-proc tree, /tmp worktrees.

## /tmp consumer-class table (measured 14:30–14:45Z, BEFORE deletion)
| Class | Entries | MB | Disposition |
|---|---|---|---|
| lane-scratch-trees (gc_p*, memoir-*, rev-*, ph6rev, rogue-*, leg7, sgc-sv, …) | 1,587,584 | 33,154 | DELETE if >48h-idle, not open, not evidence-refed |
| unknown-dirs — mostly full-repo base-extract clones (52 × ~18.5K entries / ~370 MB each, from `git archive|tar -x` gate/verify/review lanes) + test fixtures (nestops-audit-*, scheduler_test_*, ens-fake-home-*, gateconf-repo-*) | 1,233,174 | 31,663 | same fences |
| mktemp-trees (tmp.*, 1,660) | 146,146 | 6,589 | same fences |
| shakedown-fleet-* (3,859 dirs) | 7,721 | 6,349 | same fences |
| pytest-of-nea (53 numbered roots!) | 60,753 | 3,306 | per-root fences; newest-3+current kept |
| _MEI* PyInstaller extraction dirs (19) | 20,220 | 3,018 | stale ones only; open ones fenced |
| skillfix* incident family | 66,712 | 1,388 | KEPT — paused skill-keeper incident (critical note), fix lane owns |
| node-compile-cache + vscode logs | 40,857 | 378 | stale only |
| cn-review2-BSntpd | 18,470 | 376 | KEPT — live review lane cwd |
| misc loose files (misc-output, scratch-dbs tmp*.db, uv locks, probe scripts, bash-std redirects) | ~11,600 | ~33 | stale + not-open only |
| wt-sweep-rescue-20261118T0433Z | 1,220 | 32 | DELETED after byte-superset verify vs durable copy |
| protected: 9router* (user-owned, anomaly cba5462d), v0*sidecars-preserved (ceremony note), systemd/snap-private, .X11-unix family | — | — | NEVER |

## Structural findings → fix-lane candidates (ranked by inode impact)
1. **Gate/verify/review lanes extract a FULL repo tree (~18.5K files, ~370 MB) into /tmp per run and never clean up** (52 clones measured; the EVIDENCE-pok `git archive | tar -x` byte-parity pattern is the producer). FIX: lanes should extract into pytest basetemp (auto-lifecycle) or `trap rm` on exit; a sweeper would treat symptoms.
2. **pytest-of-nea accumulates numbered roots without pruning** — 53 roots measured; pytest's keep-3 retention demonstrably not effective in this fleet (live churn deleted one root mid-audit, yet 53 persisted). FIX: audit why retention is bypassed (explicit --basetemp? HOME variance?); consider a periodic prune of pytest-of-nea/pytest-* to newest-3.
3. **shakedown-fleet-\* / skill-capture-sink-session-\***: thousands of per-run session dirs with no TTL (3,859 + 3,556 members). FIX: owner lanes add cleanup or TTL sweeper.
4. **Journal .bak accumulation in releases/**: state.json.pre-* baks have no rotation (13 measured → 9 after rule applied). Rule applied 2026-11-18: keep 3 newest per operation-kind (manual/trim/hardtrim/txnrepair/reconcile) + everything from current-release era (≥2026-11-08) + anything referencing an on-disk release. FIX: rotation belongs in the journal-trim step of the upgrade machinery.
5. **Worktree census drift**: 34 worktrees at audit (Emperor counted 27); 12 merged+clean-or-sidecarred trees reclaimed same-day; keep-set has explicit reasons (see below). FIX: census rows + auto-flag for trees idle >7 days merged+clean.

## Worktree disposition (34 → 21, 2026-11-18T14:50Z)
- REMOVED (13): snapshotfix, stage-v312 (v0.31.2 ceremony complete), prompt-reload-compact-base, spike-compaction (+branch feature/spike-compaction-vs-append deleted, merged), eventloop-packs-c836334e87 ×2, soak-base (untracked leftovers all landed in latest), test-v02915, trs-a-base/head, evloop-fence-f4fa4cc8b/fence2 (unlanded bits sidecarred first), /tmp/scratchpad/src-5da1021f2.
- Sidecar rescues: /home/nea/agents-ensemble/data/evidence/wt-sidecars-20261118T1435Z/ (MANIFEST.sha256 + provenance per tree).
- KEPT (21): canonical (detached, never a commit target), ari-stop-int (local `latest` checkout, active today), central-maintenance (ENSEMBLE-35 active), decision-brief + decision-usage-guide (unmerged WIP), durability (standing in-flight commission), gc-fe-surfaces + group-chat-release (unmerged; release lane pending), hbfix (active 33min), pok-guideline-v2 (== latest tip, active), scripts-pin (ALWAYS), soak-gate (unmerged branch), v0283-evidence (unmerged tester evidence, awaits leader sweep), video-mock ×5 (unmerged, active), wake-path-carrier-fix (code-WIP, active today), /tmp: skillfix-regbase (paused incident), vmr-base3 (unmerged), wt-b2 (<48h activity).

## Live-key status (report-only)
/tmp/9router.key EXISTS — 35 bytes, mode 600, mtime 2026-11-09 07:23Z. Referenced by evidence pointers + anomaly cba5462d. Disposition USER-OWNED; untouched.

## Fences used (repeatable recipe)
1. Recency: subtree scan `find <p> -newermt <now-48h> -print -quit` — delete only zero-hit candidates (per-ENTRY mtime of top-level is insufficient).
2. Open-by-process: /proc/*/fd + cwd/exe/root readlink sweep immediately BEFORE each rm batch; skip candidate if any open path is inside it.
3. Evidence pointers: grep of `/tmp/<name>` across data/evidence/ + .agents/ + docs/ builds a protect-list (1,329 refs found; 96 candidates thereby kept).
4. Worktrees: HEAD ancestor-of origin/latest AND porcelain-clean AND zero /proc refs AND not in a live commission; dirt = sidecar-rescue (durable evidence dir, sha manifest) or keep+flag — code-WIP of active lanes never touched.
