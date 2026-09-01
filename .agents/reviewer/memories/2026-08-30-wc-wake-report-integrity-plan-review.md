# 2026-08-30 — wc-wake-report-integrity PLAN Review (APPROVED-WITH-CONDITIONS, execution gated)

Branch `feature/wc-wake-report-integrity` @ `1f8f8ed4` (plan-only; 6 files, untracked).
Deep-Review council: 2 councilors (agentic + coding), `plan-review` skill, 4 lanes + adversarial.
Verdict: **APPROVED-WITH-CONDITIONS — design approved · phase2-plan.md text REJECTED-as-written (coding dissent, preserved) · implementation gated on C1–C3.**

## Blocking conditions (mechanical bookkeeping, no redesign demanded by either councilor)
- **C1**: architecture-recommendation.md §5's 18 LOCKED C2-D2.x verdicts + leader-CONFIRMED flip policy (≤2wk soak, immediate-on-incident) never lifted into decisions.md — register still shows OPEN and *self-blocks P2b* per its own rule (decisions.md:111). Also: decisions.md:60 still *recommends* `pending_watchers`; :63 still presents `error_reporting.py`.
- **C2**: phase2-plan.md still encodes OVERRULED designs — as-written implementation would regress the silent-death defect:
  - D2.7: (b) predicate on `dependency_bus.pending_watchers` — cache-first (`dependency_bus.py:960-961`), purged post-`emit_terminal` (`:709`) → empty exactly in the inter-report gap → **predicate never fires**. Correct source: `report_injections` (PENDING/DEFERRED, promote `count_pending_for_parent` `repository.py:1042` with child-terminal JOIN + tx adaptation) corroborated by `dependency_watchers` FIRED ∧ `enqueued_at IS NULL`.
  - D2.9: (c) marker still carries directive half — self-neutralized by `_frame_injected_report`'s "NOT an instruction… Do NOT execute" frame (`graph.py:194-224`) AND erodes prompt-injection defense. Descriptive-only text.
  - D2.10: (d) still scoped to `error_reporting.py:739` — that is the child-ERROR lane (`_send_error_report`); success-lane framing there is dead code. Prompt-side 12 agents + writing-guide + dispatch-mirror instead.
- **C3**: `:1060` bypass deletion ABSENT from phase1-plan.md (grep-verified). Chain: `manager.py:6258` → `instance_messaging.py:1007` → `:1060` direct `graph.ainvoke`; zero production callers (tests only); skips `_build_graph_input`, choke point, tool-pairing guard. Test-fixture migration list: test_manager.py ~6 sites, test_inner_soul*.py, test_agent_bootstrap.py:144, test_phase4_manager_decomposition.py:795.

## Verified anchors @ 1f8f8ed4
- `_build_graph_input` `instance_messaging.py:176-243` (S4: no `prepended_msgs` seam for D2 leftover ordering); `:3407` `graph_input=None` silent-resume branch (S5 crash risk on `.messages`).
- `child_reports.py:2117-2127` inline same-tx bus gate (S7 template at `:2065-2089`) — (b) predicate must be inline same-tx after both gates.
- Claim gate `task/repository.py:1414-1428` excludes only PAUSED/TERMINATED (S9: W5 terminal-after-turn-1 edge — queued user-msg Task still claims on COMPLETED parent).
- FE 202/200: shared `next` handler branching on `response.queued` (`chat.component.ts:1154-1157`, `sse.service.ts:16-31`) → likely zero FE code change; verify as checklist item (S10).
- Citation drift pattern: architect §5 cites `:583-584` for the purge (actual `:709`, ~125 off); manager `:6245` vs live `:6258`. Semantics verified correct; re-anchor at lift time.

## Lesson
Plan sets that carry a separate "recommendation" doc with LOCKED verdicts MUST lift them into the operative plan/register files — otherwise the operative files silently encode the overruled design and pass a completeness glance while being wrong. Standing review question: **"which file is the operative text, and does it match the register of record?"** Also: register self-blocking rules (P2b gate) are checkable mechanically — always evaluate the register's own exit conditions against its rows.


# Round 2 — Delta-confirmation of the lift → ✅ CONFIRMED (2026-08-30)

1 worker (`review-worker-delta-lift`, plan-review skill) verified the planner's C1–C3 + S-fold application against the original council findings + arch-rec §5.

- All 5 checks CONFIRMED: D2.7 durable-row predicate at all 3 sites (residuals = 5 negation/rationale mentions, zero operative); 18/18 LOCKED rows machine-diffed vs §5 (14 byte-identical, 4 benign — D2.7 re-anchor `:583-584`→`:709` declared, D2.2/D2.16 backtick normalization, D2.11 cosmetic recipient phrasing); flip-policy row w/ owner+cadence+kill-switch `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED`; P2b register exit rule now satisfied; T6b + D7 + coherent T-chain; all folds placed (S4/S5/S6/S7/S8/S9/S10/S11/S13); stale-string sweep clean; Seq-AB operative vs superseded consistent; plan-overview structurally clean post-3-silent-edit-failures.
- 0 🔴 / 2 🟡 / 4 🟢. Plan cleared for approver gate.
- 🟡#1 `phase2-plan.md:226` — stale "same bus" phrasing in cross-phase note (contradicts D2.7 + own `:220` row); reword to durable queue/report layer.
- 🟡#2 `phase1-plan.md:270` — T6b fixture-list ground-truth errors: `tests/unit/tools/test_inner_soul*.py` is a PHANTOM glob (real callers: `tests/integration/test_inner_soul.py` :145/:201/:250 + `test_inner_soul_standalone.py` :258/:357); `test_agent_bootstrap.py` lives in tests/integration/ (not unit/); 3 omitted InstanceMessagingService.send_message callers = 13 sites (`test_question_deferred_pause_edge_cases.py`, `test_question_deferred_pause_callback.py`, `test_title_generation_trigger.py`). test_manager.py 8-site enumeration machine-verified EXACT.
- Lesson: verify fixture/anchor PATHS not just counts — planner globs can point at phantom files whose names collide across test trees (unit/ vs integration/). `grep -c` on the named file beats pattern inheritance.
