# LCA Stage-2: fail-open fault-injection findings (fused block outside try/except; FR-13 gaps)

Date: 2026-09-16
Gate: LCA resolver Stage 2 final merge gate — delta 0ea60d91..f926de24, branch feature/lca-resolver-stage2
Found by: Job 5 fault-injection matrix (instance 45c74774, tests/integration/test_attestation_stage2_failopen.py @ 8be94ef2, 14/14 PASS pinning actual behavior)

## F-B (hard): fused block sits OUTSIDE the gate node's try/except

The Stage-2 fused block (graph.py:5242-5524) is at the same indent level as (i.e., outside) the gate node's outer try/except (graph.py:5176). Any exception in verdict mapping, hint injection (`_make_completion_check_note_message`), or row emission (`emit_resolver_eval_row`, graph.py:5507) propagates to LangGraph — **gate crashes on all bands**. No `leader_completion_gate_error` row, no `gate_exception_seen` stamp, ledger not incremented. Caught seams by contrast: resolver-compute faults (attestation_gate.py:1483-1494) and judge-invocation faults (graph.py:5305-5319) ARE caught with loud rows.

Recommendation (not applied): wrap the fused block in its own try/except + `_persist_gate_exception_marker` (Stage-3 scope candidate).

## F-C: FR-13 deviations on Stage-2 seam faults

1. Resolver-compute faults (seams i/ii) log `leader_completion_resolver_eval_error` but do NOT fail-open-allow — deny-band proceeds DENIED with counter increment (equivalent to running without the resolver step). Deviates from FR-13 spirit ("MUST fail-OPEN on any scanner/gate exception"), within literal scope.
2. `gate_exception_seen` is stamped by NO Stage-2 seam-fault path (only the pre-Stage-2 scanner path does, test_attestation_fail_open.py:87).

## What passed

- Seams (i)(ii)(iii): gate survives, loud error rows fire (`resolver_eval_error` / `gate_fused_judge_error` with error_class), no nudge+deny double-fire, budget intact.
- DP-5 confirmed: judge-invocation fault on deny-band → deny+nudge (no fail-safe allow on deny band); marker/A bands → allow+hint.
- **F2 wakeup re-fire: PASSES** — after fault-cleared, the gate re-fires on the next evaluation (both scenarios; resolver_eval row with fired=True, judge_invoked=True).

## Harness notes

- Fault injection at 4 seams × 3 bands via monkeypatch; DP-5 path-(d) mapping pinned per cell.
- Contract clarifications elsewhere in this gate: meta-bypass predicate is a disjunction (`user_answer_pending` arm fires even with attestation_required=True); deny bound semantics `d+1 > b` (bound=3 ⇒ 4 cycles); allow+hint path ENDs the graph run without re-route (NR-6, graph.py:5480-5504) so multi-eval arcs need ≥2 ainvoke calls sharing a thread_id.
