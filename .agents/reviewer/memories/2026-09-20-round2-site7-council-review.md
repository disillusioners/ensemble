# 2026-09-20 — round-2 site-7 followup council review (61cdfb28..e3ec31a0, feature/watch-notify-site7)

Deep-Review council (2 models, `code-review`). **VERDICT: APPROVE unanimous — Hook-A placement deviation APPROVED, Hook-B APPROVED (token caveat). 0 critical, 3 medium, 6 minor.**

## Incident-path closure (verified end-to-end)
All 3 entry lanes (manager.py:7233/:7379/:10569) → `_process_child_completion_and_notify_parent` (child_reports.py:1793) → **unconditional** `await _dispatch_post_commit_side_effects` (:1925) → `outcome="root_completed"` (:2634) → branch :3846 → hook :3870-3945. The :2542 sync mint branch reaches the continuation — no early return/gating flag between commit and dispatch. Tokens: task→completed, mirror→settled via per_kind_status_for (work_resolver.py:1055-1104); terminal filter :3928-3931. Timing-soundness: resolver treats the Instance as status authority for `active` rows (work_resolver.py:1678-1684) and the root is stamped COMPLETED before dispatch.

## Approved deviation pattern (reusable)
Sync helper repo-pure on a worker thread (async facade unawaitable there — placement architecturally FORCED) + hook at the async post-commit continuation; exemption entry documents repo-purity reason (test_terminal_write_census.py:326-332). Census counterfactual RED proof method: strip the 6 round-2 fixture entries → `_assert_sites_classified` names exactly those 6; walker M-surface `INSTANCE_COMPLETION_METHODS` + S4 `ROOT_COMPLETED_LITERAL` genuinely discover the new sites.

## Follow-up lane (mediums)
- **M1 residual skip shape**: dual-backed work, JobItem `active` + instance_id None/mismatched → admission-map fallback → `processing` → §3 skip (child_reports.py:3926-3934 + work_resolver.py:1674-1691). Unpinned by both new regression tests (they seed done+instance-linked). Unproven in prod but the belt-failure genus.
- **M2 root-ERROR lane = most plausible remaining stranding genus**: error_reporting.py:505-508 no-parent early return, NO fan-out hook; walker S4 models only root_completed. Needs its own audit ticket.
- **M3 Hook-B fixed token**: job_recovery_service.py:1273-1276 hardcodes `'completed'`; F10 precondition (:1206-1208) admits done+failed/cancelled and mirror-settled. Fix: `per_kind_status_for(work_id, default="completed")` mirroring Hook A (child_reports.py:3927). One line, ideally pre-merge.

## Hygiene + operational
- Dispatcher "~158" / developer "158 passed 0 failed" NOT reproducible — actual: event_driven 17/17, census 4/4, full tests/job_queue 1746P/7F/38S; differential vs base 61cdfb28 (temp worktrees) = identical 7 pre-existing failures, zero new.
- **Outstanding operational proof: post-deploy smoke on the real stranded rows (8ade5d22 / 5923ce22)** — read-only council constraint; rows retro-heal only after merge+rebuild+restart. Close the incident note only after that smoke.
- Minors: vacuous assert (test_event_driven_completion.py:847), private-attr reach `_notify_service._repository` (:3913-3917), resolver-exception default token unconfirmed (:3926-3931), stale docstring (stale_task_recovery.py:690-694), find_jobs_by_instance ACTIVE-only (repository.py:1469), non-reproducible count claims.
