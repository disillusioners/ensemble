# Test System Optimization — Approval Tracking

Current Plan: Test System Optimization
Tracking File: test-system-optimization-tracking.md
Status: IN_PROGRESS

---

## Iteration 001 — REJECTED (2026-06-24 21:00)

### Evaluation Method
- 2 sequential council sessions (claim verification + feasibility analysis)
- Council 1: All 10 factual claims VERIFIED against source files (markers missing, deps missing, addopts correct, 4/4 stale tests confirmed)
- Council 2: Feasibility/consistency analysis found 1 critical + 2 concerns + 3 notes

### Blocking Issues

#### 1. DependencyBus fixture is broken (CRITICAL — would crash entire suite)
- **Location**: Phase 2 Task 5, `phase2-plan.md:47`
- **Problem**: Fixture calls `DependencyBus()` with no args. Production constructor (`daemon/services/dependency_bus.py:241`) requires mandatory `repository` arg. Raises `TypeError`. As `autouse=True, scope="session"`, breaks ALL ~5,675 tests at session start.
- **Fix required**:
  (a) Pass a mock/in-memory `DependencyWatcherRepository` to constructor
  (b) Use `set_dependency_bus()` (public API) instead of direct `db_mod._dependency_bus =` assignment
  (c) Make fixture **non-autouse** to avoid conflicting with 54 existing `set_dependency_bus(None)` per-test teardowns
  (d) Request fixture explicitly from the 2 files that need it

#### 2. Phase 2↔3 coupling matrix contradiction
- **Location**: `plan-overview.md:43` vs `phase2-plan.md:9-11` + `phase3-plan.md:80`
- **Problem**: Overview labels 2↔3 as "independent, different concerns entirely" but both modify `tests/conftest.py`
- **Fix required**: Relabel to "loose — shared conftest.py, coordinate insertion per W8"

### Non-blocking Notes

3. Phase 3 Task 8 (`collect_ignore_glob` for h10_l14) — redundant; file already skips cleanly via `pytestmark`. Drop task.
4. xdist 4x speedup estimate optimistic (realistic: 1.5x–2.5x). Not a correctness issue.
5. `asyncio.sleep(0.05)` in Phase 2 Task 12 — recommend `asyncio.sleep(0)` for CI determinism.

### Verified Claims (no issues)
- 6 integration test files confirmed lacking markers ✓
- pytest-timeout + pytest-xdist confirmed missing ✓
- pyproject.toml addopts confirmed ✓
- 4/4 stale test failures confirmed against production code ✓
- 4 production bugs correctly noted, not fixed ✓
- 3 phantom failures correctly identified ✓
- Savings model arithmetic internally consistent ✓
- Production code boundary (Task 9) confirmed test-only ✓
