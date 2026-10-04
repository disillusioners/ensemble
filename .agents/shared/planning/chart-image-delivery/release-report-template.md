# Release Report Template — chart-image-delivery

This file is the **template** the Phase D implementer fills in for the final release report. The filled-in instance lives at `release-report.md` in this same directory. Sections per `phaseD-plan.md` Components §6 (R2 added §6 adopted + §7 deferred+residual; R3 renumbered to nine contiguous sections §1-§9).

**Canonical section map (mirrors `decisions.md` §phase-d-release-report):**

1. **What shipped** — per-phase deliverable list (Phase A's 10 files [9 code/prompt + decisions.md verify/append], Phase B's 7 daemon files + slack-setup.md row, Phase C's **20** cardinal references + chart skill section). File lists verbatim.
2. **Test evidence** — per-suite green counts. Every suite named in `phaseD-plan.md` §Test Strategy + the audit script + the e2e standard leg + the e2e integration leg. Counts from CI/test output, not "✓".
3. **USER ACTION ITEM — Slack `files:write` scope** — verbatim 5-step operator procedure + the text-only fallback note.
4. **Restart/promote matrix** — verbatim table from `decisions.md` §phase-d-restart-promote (the canonical 6-row consolidation).
5. **Rollback notes** — Phase B is the risky surface; describes graceful degradation text-only fallback; Phase A + Phase C revert prose.
6. **Adopted-items ledger** — items promoted out of the deferred ledger during the commission (R2 NEW). Currently: `store.delete(image_id)` after successful chat delivery.
7. **Deferred-items ledger + accepted residual** — 4 deferred items + 1 ACCEPTED RESIDUAL (stale-real-id wrong-image delivery at ~1-3%, revisit trigger strip-rate >10% post-Phase C or user report).
8. **Real-platform smoke evidence** — Discord (always-on), Slack (gated on `files:write`), Telegram (gated on real bot token). Contingency N6 if invoked (interim gate = Group 7 astream e2e; release CUT HELD until real-channel smoke completes).
9. **Sign-off** — project owner + developer/tester who ran Phase D; date; release tag.

**Filling instructions:**

- §1: replace `[phase-A-commit-list]` etc. with the actual commit SHAs from `git log --oneline cf8efbef..HEAD` per the file-path scope.
- §2: paste per-suite passed/failed/skipped + exit codes from `pytest tests/<suite>.py -v` (or the audit script run); include the exact commands used and any `--override-ini="addopts=" -m <marker>` needed to defeat marker-deselection.
- §3: paste the verbatim text from `phaseD-plan.md` Components §6 §3 (5-step procedure + gating note).
- §4: paste the verbatim 6-row matrix from `decisions.md` §phase-d-restart-promote.
- §5: paste rollback prose per `phaseD-plan.md` Components §6 §5.
- §6: paste the adopted-items table from `decisions.md` §phase-d-adopted-items.
- §7: paste the deferred-items table + the ACCEPTED RESIDUAL row from `decisions.md` §phase-d-deferred-items.
- §8: paste real-channel smoke evidence (Discord screenshot + marker-stripped explanation; Slack text-only + WARN-once log if scope not granted; Telegram deferred-evidence note if bot token not provisioned). If contingency N6 invoked, paste Group 7 astream e2e interim-gate output + blocker description.
- §9: project-owner sign-off line left blank for the promote ceremony; developer/tester sign-off line carries Phase D execution context; release tag: PENDING — deferred to the release cut after giter's integration.

**Variance log:** deviations from the plan (e.g. on-branch version bump ahead of release cut, contingent smoke-gate invocation) are recorded in the "Deviations & discrepancies" appendix at the end of the filled-in report.