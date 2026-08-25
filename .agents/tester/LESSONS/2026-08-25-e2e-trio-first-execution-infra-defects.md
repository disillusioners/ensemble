# Lesson: E2e tests shipped unexecuted — infra defects surfaced only at gate time

**Context:** Integrated merge gate for `feature/pause-resume-terminate-tree-fix` (2026-08-25). The 3 branch-authored e2e acceptance tests (B2/B3/B5) had NEVER been executed by their author — B5 still carried a "NOTE — DO NOT EXECUTE / authoring only" docstring. First real execution (gate round 1) failed 3/3, threatening a false merge-block.

**Defect classes found (all test-side):**

1. **Unsupported API filter silently ignored** — helpers used `GET /api/instances?parent_id=X`; the list endpoint supports only `limit/offset/project_id/exclude_kb/search`. FastAPI ignores unknown query params → helper received the flat root-based page → "child" = arbitrary instance (in B5's case, the test's own leader). **Fix pattern:** resolve children via `GET /api/instances/{parent_id}` → `children` field, and ALWAYS assert resolved child's `parent_id` equals the expected parent before use.

2. **Permission-model collisions in prompt wordings** — spawn instructions must respect `team_members`: tester→{explorer,worker} (NOT developer); developer→{coder,explorer,...} (NOT developer); leader→{developer,tester,...} (NOT worker). A test prompt asking the impossible produces LLM improvisation (delegation, retries) that burns the window and fails setup, not the acceptance.

3. **Spec vs post-fix semantics drift (plan oversight)** — phase2-plan task 2.9 specified "children complete DURING pause"; post-B1-fix whole-tree pause CANCELS running/fresh children, making that state unconstructible for in-subtree children — while B5's acceptance explicitly pins whole-tree pause. The two plan texts were mutually exclusive. **Rule: when a fix changes cascade semantics, re-derive every dependent test's precondition for constructibility.**

4. **Count-literal overfitting** — asserting exact msg-count deltas (`==1`, `==2`) breaks when a semantic fix legitimately adds messages (leader's own final reply under a termination-finality clause; assistant/tool messages on resume). **Fix pattern:** assert `>=1` monotonic advance + a DB-level binding invariant (rows==2 delivered, no re-injection) instead of exact deltas.

5. **LLM failover tax vs tight windows** — with primary down, ~20–30s/turn tax made 60s spawn windows boundary-racy (one spawn landed at exactly the deadline). **Fix pattern:** 120s windows for LLM-dependent spawn waits under degraded endpoint conditions; keep machine-side assertions tight.

**Outcome:** 8 test-only commits later, trio PASS_ALL with zero production defects found across 4 rounds — every round's failures were fully attributed to the above classes with log/DB evidence. Gate PASS.

**Process rule going forward:** any branch-authored e2e/acceptance test must be executed at least once BEFORE the implementing session closes (or be delivered marked UNEXECUTED so the gate budgets for calibration rounds). A test that has never run is a hypothesis, not a deliverable.
