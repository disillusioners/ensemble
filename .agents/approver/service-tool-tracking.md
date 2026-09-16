# Tracking: service-tool

Plan: Service-Tool — new daemon tool category for long-lived processes
Slug: service-tool
Package: .agents/shared/planning/service-tool/ (plan-overview + phase1/2/3 + decisions + architecture-recommendation + technical-analysis; 3 research-*.md background)
Branch under review: feature/service-tool (synced to latest)

## Iteration 001 — 2026-09-15 — VERDICT: APPROVED

Mode: Plan Approval (large multi-phase package → 3 section-partitioned workers, 1 skill each)

Workers:
- approve-worker-overview (5c765be5) — plan-overview.md + decisions.md → APPROVED, 0 blocking, 9 notes
- approve-worker-design  (2dccaa76) — architecture-recommendation.md + technical-analysis.md → APPROVED, 0 blocking, 6 notes (~40 repo spot-checks; independently confirmed the plan's own 🔴 catches A1/A2/A5 as real)
- approve-worker-phases  (32fb4326) — phase1/2/3-plan.md → APPROVED, 0 blocking, 10 notes

Aggregation: 0 blocking across all workers → APPROVED. No upgrades introduced (judgment band respected).

Deduped notes (merged across workers):
1. Anchor drift (all 3 workers): cited line anchors have drifted (instance.py :4667→:4743; _ensure_postgres_columns :4787→:4833; vscode attach :1051→:1176; boot-probe config :3513→~:3855+). Plan's own mandate "re-locate by SYMBOL, not line" covers this — implementers must honor the BRANCH NOTE.
2. A12 premise wrong but self-correcting: daemon/services/timestamps.py EXISTS at HEAD (added dd594b1a via f6ca8791) despite architect's "not visible" claim; dual-branch instruction lands correctly either way. Recommend correcting the observation (f6ca8791 naive-UTC convention is load-bearing).
3. A7 CI grep-gate allowlist must include upgrade_journal.py:559 (sig-0 liveness probe — benign but will false-positive a naive kill-signal gate); job_feedback_observer.py:2866 comment mentions killpg.
4. B1 matrix miscount: CATEGORY_MODULES = 37 entries, not 46 (at both f6ca8791 and HEAD) — overstates a REJECTED alternative; verdict unaffected.
5. Zombie-state blindness: os.kill(pid,0) succeeds on zombies; daemon retains no wait handle (A4). Nearly-free fix: read state field from /proc/<pid>/stat (already opened for field 22). Implementers may adopt or document.
6. Cross-instance stop semantics unstated: name-keyed daemon-global surface, no started_by_instance_id gate — defensible per OQ#4 but should be spelled out in _full_doc_ tasks (P1/P3), incl. name squatting while running.
7. F22 macOS ps -o lstart locale/slowness: accepted for v1 (phases + overview both); consider a <N ms completion assertion to harden SC-8.
8. D6 shows FULL sweep incl. A3 reaper while F12 lands the reaper in Phase 2 — coherent (design target vs phasing) but add a one-line cross-ref to D6 so casual readers aren't misled.
9. docs/architecture/instance-lifecycle.md does not exist; Phase 3 must decide create-new vs amend job-task-pause-resume.md (closest semantic neighbor).
10. Skeleton boot-sweep contract unpinned: 1.C gate should pin "[ServiceTool] reconcile_swept alive=0 reaped=0 errors=0" on fresh-DB first boot.
11. F14 default_open follow-up + Risk #4 CODEOWNERS rule are post-merge follow-ups — surface both in 3.B.7 OPS note so they aren't forgotten.
12. Cap=10 is advisory (F18 TOCTOU) — accepted for v1; OPS note must surface as accepted limitation.
13. F15 setsid-grandchild escape: propagate into service_stop _full_doc_ during 3.B.2.
14. Phase 3.A.7 OFF-state test: add case (f) pinning that kill-switch OFF also disables inline reconciliation in service_status/service_list (stale read, no write).
15. 1.MG.1 merge-gate list should explicitly include test_repo_contract.py (the 1.A.0 frozen-interface freeze).
16. 2.B.6 cleanup_all test: assert registry populated by fixture before calling (future-proof vs bash.py refactor).
17. Hot-unwind (kill-switch OFF + restart): consider one-liner warning in service_status _full_doc_ when OFF.

Positive verifications worth preserving: triple-pin discovery verified at all 3 exact lines (:105/:158/:292); A5 phantom-mount independently confirmed real (app.state never holds manager attrs; fix matches house pattern api.py:648-682); F13 FE-zero-impact grep-verified; no pre-existing service_* tools in daemon/; alternatives reasoned not strawmenned; do-not-modify fences explicit; frozen-PyInstaller activation gap addressed with boot-probe recipe.

Final: APPROVED (iteration 001). active.md updated.
