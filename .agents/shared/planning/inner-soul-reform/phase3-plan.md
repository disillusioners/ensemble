# Phase 3: Test Coverage

## Objective
Add comprehensive tests for the new rejection logic and updated classification rules, update all 16+ breaking existing tests, and ensure zero regressions in legitimate self-modification paths.

## Coupling
- **Depends on**: Phase 1 (backend classification reform)
- **Coupling type**: tight — tests assert exact classification behavior and rejection messages defined in Phase 1
- **Shared files with other phases**: `daemon/tools/inner_soul.py` (imports from it)
- **Shared APIs/interfaces**: `_classify_request()`, `_should_redirect_to_rag()`, `_format_project_rejection()`, CLASSIFICATION_RULES, `_PERSONA_INTENT_PREFIXES`
- **Why this coupling**: Tests must match the exact rejection message format and classification outcomes from Phase 1

## Context
- Previous phase: Phase 1 delivered reformed classification rules (compound patterns + persona exemption + pre-classification heuristic), graceful rejection handler, and removed `knowledge` category
- Existing test files:
  - `tests/unit/tools/test_inner_soul_redirect.py` (995 lines) — RAG redirect tests
  - `tests/unit/tools/test_inner_soul_compound.py` — compound request splitting tests
  - `tests/unit/tools/test_inner_soul_compaction.py` — memory compaction tests
  - `tests/integration/test_inner_soul.py` — integration tests
  - `tests/integration/test_inner_soul_standalone.py` — standalone tests

---

## Tasks

### Task 1: New Test File — Project Content Rejection
**File**: `tests/unit/tools/test_inner_soul_rejection.py` (NEW)

Test that project-related content is correctly rejected regardless of RAG state.

```python
class TestProjectContentRejection:
    """Test that project-related content is rejected with helpful hints."""

    # --- Git operations (compound patterns) ---
    def test_git_push_rejected(self): ...
    def test_git_commit_rejected(self): ...
    def test_created_branch_rejected(self): ...
    def test_merged_branch_rejected(self): ...
    def test_pull_request_created_rejected(self): ...
    def test_pull_request_merged_rejected(self): ...

    # --- Task/work completion (compound patterns) ---
    def test_completed_a_build_rejected(self): ...
    def test_finished_a_task_rejected(self): ...
    def test_shipped_a_feature_rejected(self): ...
    def test_setup_complete_rejected(self): ...
    def test_deployed_to_production_rejected(self): ...
    def test_deployed_new_service_rejected(self): ...

    # --- Code changes (compound patterns) ---
    def test_refactored_code_rejected(self): ...
    def test_fixed_bug_rejected(self): ...
    def test_updated_api_endpoint_rejected(self): ...
    def test_added_database_table_rejected(self): ...
    def test_removed_schema_field_rejected(self): ...
    def test_created_py_file_rejected(self): ...

    # --- Existing project_knowledge patterns still work ---
    def test_docker_mention_rejected(self): ...
    def test_postgres_mention_rejected(self): ...
    def test_kubernetes_mention_rejected(self): ...
    def test_config_file_mention_rejected(self): ...

    # --- Rejection message format ---
    def test_rejection_includes_project_history_hint(self): ...
    def test_rejection_includes_experience_hint(self): ...
    def test_rejection_includes_inner_soul_clarification(self): ...
    def test_rejection_truncates_long_content(self): ...
```

### Task 2: New Test File — Persona Content Preservation (F1 — 25+ cases)
**File**: `tests/unit/tools/test_inner_soul_persona_preservation.py` (NEW)

**⚠️ This is the most critical test file — it verifies that F1 compound patterns + persona exemptions don't break legitimate self-reflection.**

```python
class TestPersonaContentAccepted:
    """Test that legitimate persona/behavioral content is NOT rejected.

    These are the 25+ persona sample cases required by the F1 review fix.
    Each MUST be accepted (not rejected as project_knowledge).
    """

    # ============================================
    # PERSONA-INTENT EXEMPTION CASES (F1)
    # Statements starting with self-reflection prefixes
    # ============================================

    # --- "I should..." prefix ---
    def test_i_should_be_methodical_with_tasks(self):
        """F1: 'I should be more methodical in my task approach' → ACCEPTED."""
        result = _classify_request("I should be more methodical in my task approach")
        assert result["type"] != "project_knowledge"  # NOT rejected

    def test_i_should_trust_agents_more(self):
        """'I should trust agents more on SMALL tasks' → ACCEPTED."""
        result = _classify_request("I should trust agents more on small tasks")
        assert result["type"] != "project_knowledge"

    def test_i_should_be_careful_with_deployments(self):
        """F1: 'I should be more careful with deployments' → ACCEPTED (despite 'deployments')."""
        result = _classify_request("I should be more careful with deployments")
        assert result["type"] != "project_knowledge"

    # --- "I am..." prefix ---
    def test_i_am_a_devops_agent(self):
        """F1: 'I am a DevOps agent' → identity, NOT project_knowledge."""
        result = _classify_request("I am a DevOps agent")
        assert result["type"] == "identity"

    def test_i_am_a_coder_specialist(self):
        """'I am a coder specialist' → identity."""
        result = _classify_request("I am a coder specialist")
        assert result["type"] == "identity"

    # --- "I learned that I/my..." prefix ---
    def test_i_learned_that_early_testing(self):
        """'I learned that early testing catches bugs' → persona learning, ACCEPTED."""
        result = _classify_request("I learned that early testing catches bugs")
        assert result["type"] != "project_knowledge"

    def test_i_learned_that_i_rush_too_much(self):
        """'I learned that I rush too much on SMALL tasks' → ACCEPTED."""
        result = _classify_request("I learned that I rush too much on small tasks")
        assert result["type"] != "project_knowledge"

    # --- "My approach/style/strategy..." prefix ---
    def test_my_approach_to_building_solutions(self):
        """F1: 'My approach to building solutions should be structured' → ACCEPTED."""
        result = _classify_request("My approach to building solutions should be structured")
        assert result["type"] != "project_knowledge"

    def test_my_strategy_for_endpoint_design(self):
        """F1: 'My strategy for endpoint design should be minimal' → ACCEPTED (despite 'endpoint')."""
        result = _classify_request("My strategy for endpoint design should be minimal")
        assert result["type"] != "project_knowledge"

    def test_my_tendency_is_to_overplan(self):
        """'My tendency is to overplan simple tasks' → ACCEPTED."""
        result = _classify_request("My tendency is to overplan simple tasks")
        assert result["type"] != "project_knowledge"

    # --- "I value/believe/care..." prefix ---
    def test_i_value_thorough_testing(self):
        """'I value thorough testing over speed' → ACCEPTED."""
        result = _classify_request("I value thorough testing over speed")
        assert result["type"] != "project_knowledge"

    def test_i_believe_in_clean_code(self):
        """'I believe in clean code' → ACCEPTED (despite 'code')."""
        result = _classify_request("I believe in clean code")
        assert result["type"] != "project_knowledge"

    def test_i_strive_to_be_concise(self):
        """'I strive to be concise' → ACCEPTED."""
        result = _classify_request("I strive to be concise")
        assert result["type"] != "project_knowledge"

    # --- "Be more/less..." prefix ---
    def test_be_more_concise(self):
        """'Be more concise in responses' → personality, ACCEPTED."""
        result = _classify_request("Be more concise in responses")
        assert result["type"] == "personality"

    def test_be_cozy_with_user(self):
        """'Be cozy with the user' → personality."""
        result = _classify_request("Be cozy with the user")
        assert result["type"] == "personality"

    # --- "User likes/prefers..." prefix ---
    def test_user_prefers_typescript(self):
        """'User prefers TypeScript' → user_preference."""
        result = _classify_request("User prefers TypeScript")
        assert result["type"] == "user_preference"

    def test_user_likes_concise_answers(self):
        """'User likes concise answers' → user_preference."""
        result = _classify_request("User likes concise answers")
        assert result["type"] == "user_preference"

    # --- "Remember my/your..." prefix ---
    def test_remember_my_name(self):
        """'Remember my name is Cody' → identity."""
        result = _classify_request("Remember my name is Cody")
        assert result["type"] == "identity"

    # ============================================
    # BOUNDARY CASES: persona vs project contrast
    # ============================================

    def test_contrast_learned_persona_vs_project(self):
        """Same 'I learned that' prefix, different outcomes."""
        # Persona learning → accepted
        persona = _classify_request("I learned that being direct saves time")
        assert persona["type"] != "project_knowledge"

        # Project knowledge → rejected (doesn't match persona exemption)
        project = _classify_request("The API uses REST with JSON responses")
        assert project["type"] == "project_knowledge" or project["type"] != "knowledge"

    def test_contrast_code_persona_vs_project(self):
        """'code' in persona reflection vs project context."""
        # Persona → accepted
        persona = _classify_request("I believe in clean code")
        assert persona["type"] != "project_knowledge"

        # Project → rejected
        project = _classify_request("Updated the code to handle edge cases")
        assert project["type"] == "project_knowledge"

    def test_contrast_deploy_persona_vs_project(self):
        """'deploy' in persona reflection vs project context."""
        # Persona → accepted
        persona = _classify_request("I should be more careful with deployments")
        assert persona["type"] != "project_knowledge"

        # Project → rejected
        project = _classify_request("Deployed the new build to production")
        assert project["type"] == "project_knowledge"
```

### Task 3: Update ALL Breaking Existing Tests (F5 — Full Enumeration)

**File**: `tests/unit/tools/test_inner_soul_redirect.py` (995 lines)

**⚠️ 16 tests will break** when `knowledge` category is removed. Each must be updated:

#### 3.1: `TestRAGRedirectConstants` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 1 | `test_knowledge_classifications_contains_expected_types` (L41-43) | Asserts `_KNOWLEDGE_CLASSIFICATIONS == {"knowledge", "pattern", "event", "skill", "mistake", "project_knowledge"}` | `"knowledge"` removed from set | Change assertion to `{"pattern", "event", "skill", "mistake", "project_knowledge"}` |

#### 3.2: `TestShouldRedirectToRag` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 2 | `test_knowledge_classification_with_memory_target_redirects` (L126-129) | Passes `classification={"type": "knowledge", ...}`, asserts redirect is True | `"knowledge"` type no longer produced by `_classify_request()` | **Remove test** — or update to use `"pattern"` type with `["memories"]` targets (equivalent behavior) |
| 3 | `test_knowledge_classification_with_memories_target_redirects` (L131-134) | Same as #2 with memories target | Same | **Remove test** — or update to use `"skill"` type |
| 4 | `test_empty_targets_does_not_redirect` (L200-203) | Uses `classification={"type": "knowledge", ...}` with empty targets | Type reference | Change `type` to `"pattern"` or `"event"` (any remaining `_KNOWLEDGE_CLASSIFICATIONS` member) |
| 5 | `test_reject_filtered_out_with_only_rag_targets_redirects` (L220-224) | Uses `classification={"type": "knowledge", ...}` with `["memories", "REJECT"]` | Type reference | Change `type` to `"pattern"` |
| 6 | `test_rag_disabled_never_redirects` (L236-240) | Uses `classification={"type": "knowledge", ...}` | Type reference | Change `type` to `"pattern"` |

#### 3.3: `TestClassifyRequest` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 7 | `test_knowledge_classification_i_learned_that` (L256-260) | Asserts `"I learned that early testing catches bugs"` → type `"knowledge"` | No `"knowledge"` type | "I learned that early testing..." matches persona exemption → falls to `mistake`/`pattern`/`event`. **Update assertion** to `assert result["type"] in ("mistake", "pattern", "event")` and `assert "memories" in result["targets"]` |

#### 3.4: `TestClassifyRequestIntentParameter` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 8 | `test_classify_intent_remember_affects_only_fallback` (L926-934) | Asserts `"Remember that the sky is blue"` → `"knowledge"` type or `"memory"` in targets | No `"knowledge"` type | `"Remember that the sky is blue"` no longer matches any pattern → falls to event/memories fallback. **Update assertion** to `assert result["type"] == "event"` and `assert "memories" in result["targets"]` |

#### 3.5: `TestFormatRagRedirect` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 9 | `test_output_contains_classification_type` (L392-396) | Passes `classification={"type": "knowledge", ...}`, asserts `"knowledge" in result` | Type reference | Change to `{"type": "pattern", ...}` and assert `"pattern" in result` |
| 10 | `test_output_format_is_multiline` (L430-434) | Uses `classification={"type": "knowledge", ...}` | Type reference | Change to `{"type": "pattern", ...}` |

#### 3.6: `TestInnerSoulToolRedirect` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 11 | `test_knowledge_request_redirects_to_experience` (L452-470) | Sends `"I learned that early testing catches bugs"`, asserts `"knowledge" in result.lower()` and no file created | Classification type changes | `"I learned that early testing catches bugs"` → persona exemption → `mistake`/`pattern`/`event` → RAG redirect. **Update assertion** to check `"experience()" in result` (still redirects) and `assert "knowledge" not in result.lower()` (type is no longer `knowledge`) |
| 12 | `test_content_parameter_works_for_redirect` (L604-613) | Sends `"I learned that documentation is important"`, asserts redirect | Classification type changes | Update: this still redirects (event/pattern fallback), assertion `"experience()" in result` still holds. May need to update the request to be non-persona. |

#### 3.7: `TestInnerSoulToolResponseStructure` class

| # | Test (Line) | Current Behavior | What Changes | Fix |
|---|-------------|-----------------|--------------|-----|
| 13 | `test_redirect_response_has_proper_format` (L644-658) | Sends `"I learned that keeping code simple prevents bugs"`, checks format | Classification type changes | Still redirects via event/pattern fallback. **Update**: assertion `"Classification:" in result` still holds. Remove any `"knowledge"` specific checks. |

#### 3.8: Additional tests to verify (may or may not break)

| # | Test (Line) | Concern | Action |
|---|-------------|---------|--------|
| 14 | `test_skill_classification_i_can_now` (L313-317) | `"I can now do Docker deployments"` — contains "Docker" and "deployments" | **May break!** With project pre-check, "Docker" matches existing project pattern. BUT persona exemption may not match ("I can now" is not in `_PERSONA_INTENT_PREFIXES`). **Fix**: Add `"I can now"` to persona exemption prefixes (it's a skill statement), OR change test input to `"I can now write async tests"` (no project terms). |
| 15 | `test_pattern_classification_pattern_colon` (L291-295) | `"Pattern: always when we use k8s"` — contains "k8s" | **Will break!** Project pre-check catches "k8s" before `pattern` classification. **Fix**: Add `"Pattern:"` to persona exemption prefixes, OR change test input to `"Pattern: always when we rush, we make mistakes"`. |
| 16 | `test_pattern_request_redirects` (L547-555) | `"Pattern: whenever we deploy to k8s, we see latency spikes"` | **Will break!** Contains "k8s" and "deploy". **Fix**: Same as #15 — add "Pattern:" prefix to persona exemption, OR change test input. |

**⚠️ DESIGN DECISION for tests 14-16**: The reviewer should decide:
- **Option A**: Add `"Pattern:"`, `"I can now"`, `"New skill:"`, `"Mistake:"`, `"Lesson learned:"` to `_PERSONA_INTENT_PREFIXES` so knowledge-oriented patterns always pass through to their intended classification
- **Option B**: Change test inputs to avoid project terms

**Recommendation**: **Option A** — these are all legitimate self-reflection prefixes. Add them to the persona exemption list in Phase 1 Task 3.

### Task 4: Test Classification Ordering & Persona Exemption (F1)
**File**: `tests/unit/tools/test_inner_soul_rejection.py` (add to same file)

```python
class TestClassificationOrdering:
    """Test that project_knowledge check runs BEFORE other classifications."""

    def test_project_content_with_remember_that_rejected(self):
        """'Remember that docker is configured' → project_knowledge (not knowledge)."""
        # Note: "Remember that" is NOT in persona exemption (it's "Remember my/your")
        result = _classify_request("Remember that docker is configured")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_project_content_without_persona_prefix_rejected(self):
        """Project content without self-reflection prefix → rejected."""
        result = _classify_request("The API uses REST with JSON")
        assert result["type"] == "project_knowledge"

    def test_persona_prefix_skips_project_check(self):
        """Persona prefix → skip project check → normal classification."""
        result = _classify_request("I should improve my deployment strategy")
        assert result["type"] != "project_knowledge"
```

### Task 5: Test Graceful REJECT Handler (Both RAG States)
**File**: `tests/unit/tools/test_inner_soul_rejection.py` (add to same file)

```python
class TestFormatProjectRejection:
    """Test the _format_project_rejection() function."""

    def test_rejection_has_correct_format(self): ...
    def test_rejection_truncates_long_content(self): ...
    def test_rejection_includes_classification_info(self): ...
    def test_rejection_mentions_project_history_add(self): ...
    def test_rejection_mentions_experience(self): ...

class TestRejectHandlerIntegration:
    """Test REJECT target triggers graceful rejection in both RAG states."""

    def test_project_knowledge_with_rag_disabled_returns_rejection(self):
        """When RAG is disabled, project_knowledge → rejection message."""
        # Key regression test: previously returned "Unknown target: REJECT"

    def test_project_knowledge_with_rag_enabled_still_redirects(self):
        """When RAG is enabled, project_knowledge → RAG redirect (unchanged)."""

    def test_reject_no_longer_returns_unknown_target_error(self):
        """Verify the generic 'Unknown target: REJECT' error is gone."""
```

### Task 6: Test Compound Request Per-Part Rejection (F4)
**File**: `tests/unit/tools/test_inner_soul_rejection.py` (add to same file)

```python
class TestCompoundRequestPerPartRejection:
    """Test that rejection runs per-part in compound requests (F4)."""

    def test_mixed_persona_and_project_compound(self):
        """'Be more concise AND I deployed the new build to k8s'
        → part 1 accepted (persona), part 2 rejected (project)."""
        result = inner_soul_tool.invoke({
            "request": "Be more concise AND I deployed the new build to k8s"
        })
        # Part 1 should succeed
        assert "concise" in result.lower() or "personality" in result.lower()
        # Part 2 should be rejected
        assert "reject" in result.lower() or "project" in result.lower()
        assert "experience()" in result or "project_history" in result.lower()

    def test_all_project_compound_both_rejected(self):
        """'Created a branch AND merged a pull request' → both rejected."""

    def test_all_persona_compound_both_accepted(self):
        """'Be more concise AND remember my name is Cody' → both accepted."""

    def test_persona_then_project_compound(self):
        """'I should be more patient AND completed the migration' → mixed."""
```

### Task 7: Run Full Regression Suite
**Command**: `pytest tests/unit/tools/test_inner_soul_*.py tests/integration/test_inner_soul*.py -v`

Ensure all tests pass:
- New rejection tests (Tasks 1, 4, 5, 6)
- Persona preservation tests (Task 2)
- Updated redirect tests (Task 3)
- Existing compound/compaction tests
- Integration tests

---

## Key Files
- `tests/unit/tools/test_inner_soul_rejection.py` — NEW: rejection + ordering + compound per-part tests
- `tests/unit/tools/test_inner_soul_persona_preservation.py` — NEW: 25+ persona preservation test cases
- `tests/unit/tools/test_inner_soul_redirect.py` — UPDATE: fix 16 breaking tests
- `tests/unit/tools/test_inner_soul_compound.py` — VERIFY: no regressions
- `tests/integration/test_inner_soul*.py` — VERIFY: no regressions

## Constraints
- Tests must match exact rejection message format from Phase 1's `_format_project_rejection()`
- Must test BOTH RAG-enabled and RAG-disabled paths
- False-positive rejections (persona content caught by project patterns) are the highest risk — 25+ test cases required
- All 16 breaking tests must be explicitly updated with clear change notes
- Run existing test suite BEFORE Phase 1 changes to establish baseline
- Compound per-part rejection must be tested (F4) — mixed persona+project inputs

## Deliverables
- [ ] `test_inner_soul_rejection.py` covers project content rejection (git, tasks, code, deployments)
- [ ] `test_inner_soul_rejection.py` covers classification ordering + persona exemption
- [ ] `test_inner_soul_rejection.py` covers `_format_project_rejection()` message format (both RAG states)
- [ ] `test_inner_soul_rejection.py` covers compound per-part rejection (F4) — mixed persona+project
- [ ] `test_inner_soul_persona_preservation.py` covers 25+ persona preservation cases (F1)
- [ ] `test_inner_soul_persona_preservation.py` covers boundary contrast cases (persona vs project with same keyword)
- [ ] `test_inner_soul_redirect.py` updated for all 16 breaking tests (F5)
- [ ] Full test suite passes with zero regressions
