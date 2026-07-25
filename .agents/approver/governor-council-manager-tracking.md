# Governor — Council-Manager Agent: Approval Tracking

## Iteration 001 (2026-07-25)

**Status**: APPROVED

### Evaluation Method
- Direct source-code verification of ALL load-bearing claims (C1-C6, D4, appender chain, closure mechanics, tool registry) via grep/sed against the actual codebase.
- Lines cited in the plan were checked against real line numbers.

### Source Verification Results (all CONFIRMED accurate)

| Claim | Plan citation | Verified at | Result |
|-------|--------------|-------------|--------|
| C1: `_parent_errored` sticky dict | dependency_bus.py:418 | dependency_bus.py:418 | ✅ Exact match |
| C1: `clear_parent_error()` exists | dependency_bus.py:1487-1507 | dependency_bus.py:1487-1507 | ✅ Exact match |
| C1: `had_parent_error()` exists | dependency_bus.py:1427 | dependency_bus.py:1427 | ✅ Exact match |
| C1: observer override | job_feedback_observer.py:148-151 | job_feedback_observer.py:148-151 | ✅ Exact match |
| C2: `manager.config` (no underscore) | manager.py:481 | manager.py:481 (`self.config = config`) | ✅ Exact match |
| C2: `_config` is property on lifecycle | instance_lifecycle.py:875-878 | instance_lifecycle.py:876 | ✅ Confirmed (returns `self._manager.config`) |
| C3: `_check_team_membership` returns str\|None | instance.py:248 | instance.py:248 | ✅ Exact match |
| C4: `resolve_to_id` returns str\|None | registry.py:321 | registry.py:321 | ✅ Exact match |
| C5: `create_instance_tools` is single factory | instance.py:679 | instance.py:679 (signature `(manager, current_instance_id, agent_id)`) | ✅ Exact match |
| C5: closure pins `caller_agent_id` | (implied) | instance.py:~706 (`caller_agent_id: str = agent_id or ""`) | ✅ Code explicitly handles shadowing |
| C6: `ConfigDict(extra="ignore")` | registry.py:138-140 | registry.py:138-140 | ✅ Exact match |
| C6: `context_injection` field+loader | registry.py:129 + 270 | registry.py:129 + 270 | ✅ Exact match (template for inject_allowed_models) |
| D4: WorkerPool=4 | constants.py:48-50, worker_pool.py:986 | worker_pool.py:986 (`num_workers: int = 4`) | ✅ Confirmed |
| Appender chain has manager+agent_meta | instance_lifecycle.py:771 | instance_lifecycle.py:771 (both are kwargs) | ✅ Confirmed |
| `CATEGORY_MODULES` dict | _tool_registry.py:207 | _tool_registry.py:207 | ✅ Exact match |
| `register_tool_category` | _tool_registry.py:18 | _tool_registry.py:18 | ✅ Exact match |
| `manager.spawn_instance` returns 2-tuple | (Phase 2 unpacks it) | manager.py:4118 delegates to lifecycle | ✅ Confirmed (validated_model_override returned) |

### Verdict: APPROVED

**Rationale:**
1. **Correctness** — No internal contradictions. All cross-phase contracts in Phase 0 are consistent with the implementation details in Phases 1-3. The closure-variable handling (`caller_agent_id` vs `agent_id`) is correctly identified as a shadowing pitfall, and the plan's Task 6 (Phase 2) explicitly requires the implementer to verify exact variable names.
2. **Completeness** — All stated requirements addressed: strict model validation, fault-tolerance (C1 mitigation), crash recovery (D8), quorum/deadline (D9), model dedup (D10), backward compatibility. Integration tests cover every C-fix.
3. **Feasibility** — Every interface the plan references exists at the cited line numbers with the cited signatures. The insertion points (appender chain position 5, CATEGORY_MODULES entry, tools list) are all real, accessible, and correctly scoped.
4. **Safety** — Backward compatibility preserved (spawn_instance untouched). The C1 TOCTOU is acknowledged and acceptable. The prompt-based control flow (D6) lacks runtime enforcement, but this is explicitly acknowledged and listed as a Phase 4 hardening task (non-blocking for v1).

### Non-blocking observations (NOT rejection reasons)
- D6 (markdown control flow): max-4, max-2-rounds, quorum, and clear-errors-ordering rely on LLM compliance. A misbehaving governor could violate these. The plan correctly defers runtime enforcement to Phase 4 hardening. This is acceptable for v1.
- The council summary session returned a stub (subagent timeout) rather than a synthesized verdict. This did not block approval because direct source verification — which is stronger evidence — was performed independently and confirmed every claim.
