# Decisions: pause-resume-terminate-tree-fix

Decision log for the batch. Sources: architect review `architecture-recommendation.md` @ 8abca8b5 (§0 decision summary, §10 pending items) + leader rulings relayed 2026-08-24. Incorporated into plans at Rev 2.

---

## Leader decisions (on architect §10 pending items)

| ID | Question | Decision | Recorded in |
|----|----------|----------|-------------|
| **D1** | Product sign-off: hard-delete snapshot expansion (root hard-delete now sweeps completed-descendant checkpoints; previously survived) | **ACCEPTED** — behavior change documented as intentional | phase1-plan.md T4 acceptance + AF1 flag resolution (C7) |
| **D2** | B7(b) `completed_at` re-stamp disposition | **Working-as-designed, PENDING the 30-min repro-DB check** (inspect twice-re-stamped jobs' admission history for `rearm_with_lock` transit). Flip condition documented: if the re-stamped jobs never transited `active`, an unguarded raw UPDATE exists that the audit missed → original option-B wiring becomes correct | phase3-plan.md §B7(b) + task 3.9 (Rev 2) |
| **D3** | Dead-letter terminal_reason vocabulary | **`'failed'`** (canonical, `_STATUS_CANONICAL_MAP` work_status.py:66-122). Signals *delivery did not succeed* — the operator-visible fact. `'cancelled'` remains an architect-acceptable alternative with no structural consequence | phase1-plan.md Task 8 (Rev 2) |
| **D4** | Lane-5 (revive-first for DEFERRED) vs new dead-letter sweep coherence | **DEFER** — follow-up ticket **FT-005** (align revive-first/dead-letter policies or document the asymmetry as intentional: pause-drop vs terminate-drop provenance) | phase1-plan.md risks + phase2-plan.md risks (Rev 2) |
| **D5** | AF1 lineage end-state | **AF1-A: permanent-only with staged deprecation + governance.** 5 mutation sites → `get_tree_ids_permanent()` behind `get_cascade_tree_ids()` wrapper; `get_tree_ids()` kept during migration with corrected docstring, then deprecated (marker → removal after one release cycle) once V1 fast-follow + O1 verification pass land; no new hierarchy readers. Documented flip condition: ONLY if O1 verification shows observer Tier-2 cleanup harmed by terminal descendants → forever-dual end-state; zero mechanical cost either way | phase1-plan.md approach + AF1 flag (Rev 2) |

## Architect resolutions (flags → answers)

| Flag | Resolution (source §) |
|------|----------------------|
| AF1 | Staged deprecation (§1.1-1.3); deciding evidence: revive path never re-inserts hierarchy rows; `job_tree` already renders from `parent_id`; `list_parents_with_active_children` zero callers. Leader D5 adopts |
| AF2 | Axis 1 = **1a + 1c** (enqueue guard at verified seam `child_reports.py:2638-2663` + secondary seams `manager.py:6829-6945` + drift sweep pattern (e)); Axis 2 = **2a** silent dead-letter with payload retention (§2.2, §2.4). Claim-time carve-out REJECTED (graph-turn risk on TERMINATED instances); cleanup-only REJECTED (provably insufficient — cleanup bucket 4 requires `EXISTS job_queue_items`) |
| Q1 | CONFIRMED with atomicity caveat — `_recover_fired_unsent` single-invariant holds, but NO lane backstops deliver-before-compact (Lane 2 NOT EXISTS excludes FIRED rows) → deliver loop atomic with DELETE (§3.1) |
| Q2 | MODIFY — `Outcome.status` NOT in `follow_up_payload`; encode `FollowUp.metadata["child_outcome"]="terminated"` at construction site (§3.1) |
| Q3 | CONFIRMED — gate clears via `count_pending_for_target_sync==0`; guard sites corrected to `child_reports.py:1775/:1845`; metadata must surface through graph-node drain for parent-LLM visibility (§3.1) |
| Q4 | CONFIRMED — per-target signature, symmetric to `cancel_for_target` (§3.1) |
| Q5 | CONFIRMED — revival touches neither `dependency_watchers` nor `report_injection`; FIRED obligation stays terminal (§3.1) |
| Q6 | CONFIRMED optional — guarded SELECT in; companion reconciler only if test 2.6 reveals stranded-PAUSED (§3.1) |
| AF-B5 | SUBTREE via `cascade_to_root: bool = True` boolean param (§4.1); node-only rejected outright. NEW: 5 internal callers make the default load-bearing (messaging/watchover) → unit cases 7/8 added |
| AF-B6 | Probe-first timebox (§5.2): 5 ordered probes ≤30min each; 404-body classifier is decisive first step; H1 harness-artifact = top hypothesis (static analysis rules out the DB-read seam). "No small seam → ticket" ACCEPTED at 4h cap |
| AF-P3-7 | `preserve_completed_at: bool = False` MANDATORY; **task 3.8 (wiring True at 3 call sites) DELETED** — `rearm_with_lock` EXISTS (F9 closed: `job_queue/repository.py:1974-2167`, observer call `:1470-1474`); preserve-on-re-complete freezes failure-time stamps as completions on retried jobs (§6.1-6.2). Plan's original factual premise (ZERO grep matches / F9 deferred) was false — corrected |

## Correctness-gating corrections folded at Rev 2 (register §7)

1. 🔴 P1 Task 3 → enumerate-first terminate restructure (AF1-C1)
2. 🔴 P1 Task 8(a) → `DeadLetterTurn` / cold-path replaces `fail_task→AbortTurn` no-op (AF2-C1)
3. 🔴 P1 Task 8 → companion injection-row disposition, else Lane-3/4 perpetual re-sweep + inflated `recovered` (AF2-C3)
4. 🔴 P2 Task 2.4 → two-pass cutoff (no-grace deliver pass) + atomic-with-DELETE (Q-caveats)
5. 🔴 P3 sketch lines 157/160/193 + overview §4.4 → BOTH branches use `get_cascade_tree_ids()` (AF-B5)
6. 🔴 P3 task 3.8 DELETED; default `False` mandatory (AF-P3-7)

## New follow-up tickets minted

| Ticket | Content | Source |
|--------|---------|--------|
| FT-004 | Kill-switch `ENSEMBLE_CASCADE_LINEAGE` removal (~+30 days post-soak + V1/O1 decisions); hardening at boot | AF1 §1.6 (C4) |
| FT-005 | Lane-5-vs-sweep policy coherence (align or document asymmetry) | Leader D4 |
| FT-001/002/003 | (pre-existing Rev 1) B7a future-dated rows; B7c status disagreement; SSE fan-out | P3 Rev 1 |

## Blueprint amendment queued (post-P1, C6)

"Query-time lineage survives revive" needs refinement: `instance_hierarchy` is a lossy spawn-to-report edge table under revive (revive never re-inserts rows); `instances.parent_id` is the lineage authority; no new hierarchy readers permitted.
