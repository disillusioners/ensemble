# Migration Script and Test Dependencies — `coder → developer` Alias Removal

> **Investigation Date**: 2026-07-10
> **Project**: agents-ensemble
> **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
> **Task**: READ-ONLY investigation — no files modified.
> **Report Number**: 02-migration-tests.md

---

## CRITICAL CONTEXT: Two Distinct "coder" Entities

There are **TWO separate** things called "coder" in the codebase:

1. **`agents/coder/`** — A **standalone direct-coding agent** added in commit `a5a2b01a` ("feat: add standalone coder agent — direct coding without OpenCode"). Has `meta.json` with `id: "coder"`. This is a **real, separately-registered agent** that works directly with files and bash, WITHOUT OpenCode delegation.

2. **`AGENT_ID_ALIASES = {"coder": "developer"}`** — An **alias mapping** added in commit `9ed7fdc3` ("feat: add coder→developer rename DB migration and registry alias") to provide backward-compatibility after the original "coder" agent was renamed to "developer". The alias **shadows** the standalone `agents/coder/` agent.

**The alias shadows the real coder agent.** When `resolve_pure_id("coder")` is called, it returns "developer" (via alias), even though `agents/coder/` is a registered agent with `id: "coder"`. This is a **latent bug** introduced when the standalone coder agent was added after the alias.

**Implication for removal**: Removing the alias would:
- Make `resolve_pure_id("coder")` return "coder" (the standalone agent) — **this is the correct behavior**
- Restore the standalone coder agent to working order
- Require updating all tests that assume the alias exists

---

## PART 1: Migration Script Analysis

### FILE: `scripts/migrate_coder_to_developer.py`

**What it does, step by step:**

1. **Purpose**: One-time migration that renames `agent_id='coder'` → `'developer'` across all 6 database tables. Supports both PostgreSQL and SQLite (auto-detects from connection URL).

2. **Tables modified**:
   - `instances` — `agent_id` and `agent_dir` columns
   - `instance_mappings` — `agent_id` and `agent_dir` columns
   - `job_queue_items` — `agent_id` and `agent_dir` columns
   - `dead_letter_items` — `agent_id` and `agent_dir` columns
   - `projects` — `creator_agent_id` column
   - `jobqueue` (legacy) — `agent_id` and `agent_dir` columns

3. **Mechanism**: Uses SQL `UPDATE ... SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'`

4. **Usage**: `python scripts/migrate_coder_to_developer.py [--dry-run] [--db-url URL]`

5. **Is it a one-time migration that already ran?** Unknown — the script has no state tracking. It is **idempotent** (safe to re-run).

6. **Can it be kept as-is for historical reference?** YES. The script is standalone and self-contained. It does NOT reference `AGENT_ID_ALIASES` or the registry. It only does raw SQL UPDATEs.

7. **Does it reference AGENT_ID_ALIASES or the registry?** NO. Only raw SQL strings.

8. **Related production code paths**:
   - `daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql` — SQLite production migration
   - `daemon/manager.py:2040-2046` — PostgreSQL runtime migration in `_ensure_postgres_columns()`

9. **Tables/rows modified**: 6 tables, all rows where `agent_id='coder'` are updated to `'developer'`.

**Can the migration script be kept as-is?** YES. It has no dependency on `AGENT_ID_ALIASES`. It is a standalone SQL migration script.

---

## PART 2: Test Files Found

The following test files contain references to the `coder → developer` alias system:

| File | Lines | Pattern | Test Count | NEEDS UPDATE |
|------|-------|---------|-----------|-------------|
| `tests/test_registry.py` | 644-695 | `TestAgentIdAliasBackwardCompatibility` | 7 | **YES** |
| `tests/unit/test_coder_developer_migration.py` | 1-903 | `TestCoderDeveloperMigration` + alias tests | 11 | **YES** (Part B only) |
| `tests/test_spawn_team_members.py` | 1-663 | `TestTeamMembersAuthorization` + alias tests | 13 | **YES** (alias tests only) |
| `tests/test_spawn_instance_validation.py` | 1-115 | standalone functions | 3 | **YES** |
| `tests/test_models.py` | 1-565 | `TestInstanceCreate`, `TestModelValidation`, etc. | 9 | **YES** |
| `tests/test_api.py` | 1-1194 | standalone async functions | 2 | **YES** |
| `tests/conftest.py` | 1-622 | fixtures | 2 fixtures | **YES** |
| `tests/unit/test_coder_agent.py` | 1-493 | `TestCoderAutoDiscovery`, etc. | ~39 | **NO** |
| `tests/test_spawn_instance_instructive_errors.py` | 295-315 | `TestValidAgentId` | 1 | **YES** |
| `tests/test_loader.py` | 835-845 | comments only | 0 | **NO** |
| `tests/unit/test_wanderer_agent.py` | 425-435 | `test_soul_mentions_future_coder_delegation_note` | 1 | **CONDITIONAL** |
| `tests/job_queue/test_status_alias_mapping.py` | 1-465 | `TestNormalizeStatusesAliases` etc. | 0 | **NO** (job status aliases, not agent aliases) |
| `tests/job_queue/test_idempotent_enqueue.py` | 651-652 | comments only | 0 | **NO** |
| `tests/job_queue/conftest.py` | 332 | comment only | 0 | **NO** |

---

## PART 3: Detailed Test-by-Test Report

---

### FILE: `tests/test_registry.py`

#### CLASS: `TestAgentIdAliasBackwardCompatibility`

All 7 tests in this class directly test the `AGENT_ID_ALIASES` behavior.

---

**TEST 1**
```
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_resolve_pure_id_alias
ASSERTION: registry.resolve_pure_id("coder") == "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Remove this test entirely. It tests alias resolution that would no longer exist after alias removal. If the alias is removed, resolve_pure_id("coder") should return "coder" (the standalone agent), not "developer".
RISK: HIGH — this is a core alias test that would break immediately

TEST 2
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_resolve_path_to_id_alias
ASSERTION: registry.resolve_path_to_id("./agents/coder") == "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Remove this test entirely. After alias removal, ./agents/coder would resolve to "coder" (the standalone agent's directory), not "developer". The standalone coder agent exists at agents/coder/ with meta.json id="coder".
RISK: HIGH

TEST 3
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_exists_alias
ASSERTION: registry.exists("coder") is True
NEEDS UPDATE: NO (but subtle)
UPDATE DESCRIPTION: This test would STILL PASS after alias removal because the standalone coder agent exists and is registered. However, the REASON for it passing changes: currently it returns True because "coder" exists via alias→developer→registry lookup. After removal, it returns True because the standalone coder agent is directly registered.
RISK: LOW — behavior preserved, just different reason

TEST 4
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_get_resolved_alias
ASSERTION: registry.get_resolved("coder") is not None and resolved.id == "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Remove this test. After alias removal, get_resolved("coder") would return the standalone coder agent's metadata (with id="coder"), not developer.
RISK: HIGH

TEST 5
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_get_resolved_canonical
ASSERTION: registry.get_resolved("developer") == registry.get("developer")
NEEDS UPDATE: NO
UPDATE DESCRIPTION: This tests the canonical developer agent resolution, which is unaffected by alias removal.
RISK: NONE

TEST 6
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_get_resolved_unknown_returns_none
ASSERTION: registry.get_resolved("definitely-not-an-agent") is None and registry.get_resolved("ghost-alias") is None
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests unknown ID handling. After alias removal, "ghost-alias" would still return None (not aliased to anything). Behavior unchanged.
RISK: NONE

TEST 7
FILE: tests/test_registry.py
CLASS: TestAgentIdAliasBackwardCompatibility
FUNCTION: test_instance_create_normalizes_alias
ASSERTION: InstanceCreate(agent_id="coder").agent_id == "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: This test verifies the InstanceCreate Pydantic validator normalizes "coder" → "developer". After alias removal, the normalize_agent_id validator in daemon/models/instance.py (which uses AGENT_ID_ALIASES) must also be removed. This test should be removed or replaced with a test that verifies "coder" is NOT normalized (i.e., InstanceCreate(agent_id="coder").agent_id == "coder").
RISK: HIGH
```

---

### FILE: `tests/unit/test_coder_developer_migration.py`

This file has TWO distinct sections:

**Part A (lines 355-522): `TestCoderDeveloperMigration`** — Tests the DB migration SQL. These tests do NOT depend on `AGENT_ID_ALIASES` in the runtime. They insert rows with `agent_id="coder"` and verify the migration updates them to "developer".

**Part B (lines 524-903): `TestRestoreInstanceWithAlias` + `TestJobQueueEnqueueWithAlias`** — Tests that the alias resolution fix works for stale DB rows. These DO depend on `AGENT_ID_ALIASES`.

---

#### CLASS: `TestCoderDeveloperMigration` (Part A — NO alias dependency)

```
FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestCoderDeveloperMigration
FUNCTION: test_migration_updates_coder_to_developer
ASSERTION: After running the SQLite migration, agent_id changes from "coder" to "developer" and creator_agent_id changes from "coder" to "developer"
NEEDS UPDATE: NO
UPDATE DESCRIPTION: This tests the SQL migration (reading the .sql file directly), not the runtime alias system. The migration SQL is standalone and unaffected by AGENT_ID_ALIASES removal.
RISK: NONE

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestCoderDeveloperMigration
FUNCTION: test_migration_idempotent
ASSERTION: Running migration twice produces no errors and final state is "developer"
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests migration idempotency. No alias dependency.
RISK: NONE

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestCoderDeveloperMigration
FUNCTION: test_migration_no_coder_rows
ASSERTION: Migration on empty DB produces no errors
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Edge case test. No alias dependency.
RISK: NONE

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestCoderDeveloperMigration
FUNCTION: test_migration_covers_all_tables
ASSERTION: Migration updates all 5 tables (instances, instance_mappings, job_queue_items, dead_letter_items, projects)
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests comprehensive table coverage. No alias dependency.
RISK: NONE

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestCoderDeveloperMigration
FUNCTION: test_migration_dual_engine
ASSERTION: Migration produces identical results on SQLite and PostgreSQL
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Dual-engine consistency test. No alias dependency.
RISK: NONE
```

#### CLASS: `TestRestoreInstanceWithAlias` (Part B — DEPENDS on alias)

```
FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestRestoreInstanceWithAlias
FUNCTION: test_restore_instance_with_coder_agent_id_does_not_raise
ASSERTION: _restore_instance() with stale DB row (agent_id="coder") must not raise. Verifies resolve_pure_id("coder") is called and returns "developer".
NEEDS UPDATE: YES
UPDATE DESCRIPTION: This test simulates a partially-migrated DB (stale "coder" rows). After alias removal, resolve_pure_id("coder") would return "coder" (the standalone agent), NOT "developer". The test's mock is configured so resolve_pure_id("coder") → "developer". After alias removal, the actual resolve_pure_id("coder") would return "coder", which does NOT match the standalone agent's path ("/agents/coder"). The fix being tested (using resolve_pure_id before registry.get) would still work, but the TEST's assumptions about what "coder" resolves to would be wrong. The test should be updated to: (1) remove the alias assumption, or (2) test that stale "coder" rows now fail gracefully (since the standalone coder agent exists and is different from the old developer).
RISK: HIGH

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestRestoreInstanceWithAlias
FUNCTION: test_restore_instance_with_developer_agent_id_still_works
ASSERTION: _restore_instance() with canonical "developer" works correctly
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests canonical developer agent. No alias dependency.
RISK: NONE
```

#### CLASS: `TestJobQueueEnqueueWithAlias` (Part B — DEPENDS on alias)

```
FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestJobQueueEnqueueWithAlias
FUNCTION: _make_mock_registry_resolve_coder_to_developer
ASSERTION: Fixture helper that mocks registry so resolve_pure_id("coder")→"developer", get_resolved("coder")→developer metadata
NEEDS UPDATE: YES
UPDATE DESCRIPTION: This is a fixture helper, not a test. It must be updated or removed. The mock maps "coder"→"developer" which is the alias behavior. After alias removal, this mock would need to map "coder"→"coder" (standalone agent) or be removed entirely.
RISK: HIGH

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestJobQueueEnqueueWithAlias
FUNCTION: test_enqueue_with_coder_agent_id_succeeds
ASSERTION: enqueue(agent_id="coder") must not raise ValueError. Creates job with resolved agent_id="developer", agent_dir="/agents/developer".
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, enqueue(agent_id="coder") would create a job with agent_id="coder" and agent_dir="/agents/coder" (the standalone coder agent). The test assertion `call_kwargs["agent_id"] == "developer"` would fail. Update to assert `call_kwargs["agent_id"] == "coder"` and `call_kwargs["agent_dir"] == "/agents/coder"`. The test's purpose changes from "stale alias resolves to developer" to "direct coder agent works".
RISK: HIGH

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestJobQueueEnqueueWithAlias
FUNCTION: test_enqueue_with_coder_and_idempotency_key_succeeds
ASSERTION: Same as above but with idempotency_key. Creates job with resolved agent_id="developer", agent_dir="/agents/developer".
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Same as above — update assertions to expect "coder" and "/agents/coder".
RISK: HIGH

FILE: tests/unit/test_coder_developer_migration.py
CLASS: TestJobQueueEnqueueWithAlias
FUNCTION: test_enqueue_with_developer_agent_id_still_works
ASSERTION: enqueue(agent_id="developer") works correctly
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests canonical developer agent. No alias dependency.
RISK: NONE
```

---

### FILE: `tests/test_spawn_team_members.py`

#### CLASS: `TestTeamMembersAuthorization`

```
FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_valid_spawn_leader_can_spawn_developer
ASSERTION: leader can spawn developer (developer is in leader.team_members)
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests canonical spawn. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_valid_spawn_leader_can_spawn_each_team_member
ASSERTION: leader can spawn all agents in team_members list
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests all team members including "developer". No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_invalid_spawn_leader_cannot_spawn_leader
ASSERTION: leader cannot spawn leader (not in team_members)
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Authorization test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_valid_spawn_leader_can_spawn_explorer
ASSERTION: leader can spawn explorer
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Authorization test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_invalid_spawn_developer_cannot_spawn_non_team_targets
ASSERTION: developer (team_members=["explorer"]) cannot spawn non-team targets
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Authorization test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_restricted_team_members_rejects_non_team_spawns
ASSERTION: tester (team_members=["explorer"]) cannot spawn developer
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Authorization test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_unknown_caller_agent_is_denied
ASSERTION: Unknown agent_id as caller is denied
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Authorization test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_unknown_requested_agent_is_denied
ASSERTION: Unknown agent_id as requested target is denied
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Authorization test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_alias_request_resolves_to_canonical_id
ASSERTION: spawn_instance(agent_id="coder") succeeds because "coder"→"developer" alias and "developer" is in leader.team_members
NEEDS UPDATE: YES
UPDATE DESCRIPTION: This is the key alias test. After alias removal, "coder" would NOT resolve to "developer". The standalone coder agent (team_members=[]) would be resolved instead. The test would fail. Update options: (1) Remove this test, (2) Change the assertion to test that leader CAN spawn the standalone coder agent (since developer→coder shadow relationship would be broken), (3) Change the test to verify that "coder" resolves to the standalone coder agent which has team_members=[] (empty) — so leader spawning coder would succeed since coder is not in leader.team_members (leader.team_members has "developer", not "coder").
RISK: HIGH

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_alias_caller_resolves_to_canonical_id
ASSERTION: Caller with agent_id="coder" (alias) resolves to "developer" (empty team_members), so spawn is denied
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, caller_agent_id="coder" resolves to "coder" (standalone). Standalone coder has NO team_members (empty list). Empty team_members = deny-by-default. The test would STILL PASS, but the reason changes: currently passes because coder→developer (empty list), after removal passes because coder itself has empty team_members. The assertion `assert "developer" in result.lower()` would need updating since the error message would reference "coder" not "developer".
RISK: MEDIUM

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_empty_agent_id_request_rejected
ASSERTION: Empty agent_id is rejected
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Input validation test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersAuthorization
FUNCTION: test_missing_caller_agent_id_rejected
ASSERTION: Empty caller_agent_id is rejected
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Input validation test. No alias dependency.
RISK: NONE
```

#### CLASS: `TestTeamMembersRegistryParsing`

```
FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersRegistryParsing
FUNCTION: test_leader_team_members_parsed
ASSERTION: leader.team_members contains expected agents
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Parsing test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersRegistryParsing
FUNCTION: test_developer_team_members_has_explorer
ASSERTION: developer.team_members == ["explorer"]
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests developer agent's team_members. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersRegistryParsing
FUNCTION: test_planner_team_members_has_explorer
ASSERTION: planner.team_members == ["explorer"]
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests planner agent's team_members. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestTeamMembersRegistryParsing
FUNCTION: test_all_agents_have_team_members_field
ASSERTION: Every registered agent has team_members attribute
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Schema validation. No alias dependency.
RISK: NONE
```

#### CLASS: `TestCheckTeamMembershipUnit`

```
FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_returns_none_when_allowed
ASSERTION: _check_team_membership("leader", "developer") returns None (allowed)
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Unit test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_returns_error_when_denied
ASSERTION: _check_team_membership("leader", "leader") returns error
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Unit test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_returns_error_when_requested_not_in_restricted_team
ASSERTION: _check_team_membership("developer", "leader") returns error
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Unit test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_returns_error_for_truly_empty_team_members
ASSERTION: Empty team_members denies all spawns
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Security test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_returns_error_for_missing_team_members_attribute
ASSERTION: team_members=None is treated as empty list
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Edge case test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_alias_request_canonicalizes
ASSERTION: _check_team_membership("leader", "coder") returns None (coder→developer→in team_members)
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, _check_team_membership("leader", "coder") would resolve "coder" to the standalone coder agent. Standalone coder is NOT in leader.team_members (which contains "developer"). So the result would be an ERROR, not None. The test assertion needs updating or the test should be removed.
RISK: HIGH

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_alias_caller_canonicalizes
ASSERTION: _check_team_membership("coder", "leader") returns error, message mentions "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, "coder" resolves to the standalone coder agent (empty team_members). The error message would reference "coder" instead of "developer". The assertion `assert "developer" in err` would fail. Update to assert "coder" in err.
RISK: MEDIUM

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_unknown_caller_returns_error
ASSERTION: Unknown caller is denied
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Unit test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_unknown_request_returns_error
ASSERTION: Unknown request is denied
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Unit test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_case_sensitive_agent_id_fails_closed
ASSERTION: "Developer" (capital D) is rejected
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Security test. No alias dependency.
RISK: NONE

FILE: tests/test_spawn_team_members.py
CLASS: TestCheckTeamMembershipUnit
FUNCTION: test_whitespace_in_agent_id_fails_closed
ASSERTION: "developer " (trailing space) is rejected
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Security test. No alias dependency.
RISK: NONE
```

---

### FILE: `tests/test_spawn_instance_validation.py`

```
FILE: tests/test_spawn_instance_validation.py
CLASS: N/A (standalone functions)
FUNCTION: test_backward_compatibility
ASSERTION: resolve_to_id("./agents/coder") == "developer" and resolve_to_id("agents/coder") == "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, these would return "coder" (the standalone agent). The path "./agents/coder" points to the standalone coder agent's directory. Update to assert == "coder".
RISK: HIGH

FILE: tests/test_spawn_instance_validation.py
CLASS: N/A (standalone functions)
FUNCTION: test_new_feature_agent_id
ASSERTION: resolve_to_id("coder") == "developer" and metadata.path contains "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, resolve_to_id("coder") would return "coder". The standalone coder agent exists at /agents/coder/. Update assertions to assert == "coder" and metadata.path contains "coder".
RISK: HIGH

FILE: tests/test_spawn_instance_validation.py
CLASS: N/A (standalone functions)
FUNCTION: test_edge_cases
ASSERTION: registry.get("nonexistent") is None, resolve_to_id("") is None, resolve_to_id(None) is None
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Edge case tests. No alias dependency.
RISK: NONE
```

---

### FILE: `tests/test_models.py`

#### CLASS: `TestInstanceCreate`

```
FILE: tests/test_models.py
CLASS: TestInstanceCreate
FUNCTION: test_instance_create_required
ASSERTION: InstanceCreate(**sample_instance_create_data) has agent_id == "developer" (coder normalized)
NEEDS UPDATE: YES
UPDATE DESCRIPTION: sample_instance_create_data has agent_id="coder". After alias removal and InstanceCreate.normalize_agent_id removal, InstanceCreate(agent_id="coder").agent_id would be "coder". Update assertion to assert == "coder".
RISK: HIGH

FILE: tests/test_models.py
CLASS: TestInstanceCreate
FUNCTION: test_instance_create_optional
ASSERTION: InstanceCreate with custom instance_id normalizes coder→developer
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Same as above. Update assertion.
RISK: HIGH

FILE: tests/test_models.py
CLASS: TestInstanceCreate
FUNCTION: test_instance_create_serialization
ASSERTION: model_dump() contains "developer" (coder normalized)
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Update to expect "coder" in serialized data.
RISK: HIGH

FILE: tests/test_models.py
CLASS: TestInstanceCreate
FUNCTION: test_instance_create_validation_missing_agent_dir
ASSERTION: InstanceCreate() raises ValueError (missing required field)
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Validation test. No alias dependency.
RISK: NONE
```

#### CLASS: `TestModelValidation`

```
FILE: tests/test_models.py
CLASS: TestModelValidation
FUNCTION: test_model_validate
ASSERTION: InstanceCreate.model_validate({"agent_id": "coder", ...}).agent_id == "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, model_validate would NOT normalize "coder" to "developer". Update to assert == "coder".
RISK: HIGH

FILE: tests/test_models.py
CLASS: TestModelValidation
FUNCTION: test_model_dump_json
ASSERTION: InstanceCreate(agent_id="coder").model_dump_json() contains "developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Update to expect "coder" in JSON output.
RISK: HIGH
```

#### CLASS: `TestInstanceCreateProjectId`

```
FILE: tests/test_models.py
CLASS: TestInstanceCreateProjectId
FUNCTION: test_instance_create_with_project_id
ASSERTION: InstanceCreate(agent_id="coder", project_id="proj-123").project_id == "proj-123"
NEEDS UPDATE: YES (comment only)
UPDATE DESCRIPTION: The test passes but the comment says "agent_id 'coder' is normalized to 'developer'". After alias removal, this comment is obsolete. Update comment to remove alias reference.
RISK: LOW

FILE: tests/test_models.py
CLASS: TestInstanceCreateProjectId
FUNCTION: test_instance_create_project_id_defaults_to_none
ASSERTION: InstanceCreate(agent_id="coder").project_id is None
NEEDS UPDATE: YES (comment only)
UPDATE DESCRIPTION: Same — update obsolete comment.
RISK: LOW

FILE: tests/test_models.py
CLASS: TestInstanceCreateProjectId
FUNCTION: test_instance_create_serialization_includes_project_id
ASSERTION: model_dump includes project_id
NEEDS UPDATE: YES (comment only)
UPDATE DESCRIPTION: Same — update obsolete comment.
RISK: LOW

FILE: tests/test_models.py
CLASS: TestInstanceCreateProjectId
FUNCTION: test_instance_create_serialization_project_id_none
ASSERTION: model_dump includes project_id as None
NEEDS UPDATE: YES (comment only)
UPDATE DESCRIPTION: Same — update obsolete comment.
RISK: LOW
```

---

### FILE: `tests/test_api.py`

```
FILE: tests/test_api.py
CLASS: N/A (standalone async functions)
FUNCTION: test_create_instance_success
ASSERTION: POST /instances with {"agent_id": "coder"} returns 201, and mock_manager.spawn_instance_with_mcp is called with agent_id="developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: After alias removal, the validator in InstanceCreate would NOT normalize "coder" to "developer". The API endpoint would pass "coder" to the manager. The assertion `call_kwargs["agent_id"] == "developer"` would fail. Update to assert == "coder". Alternatively, if the desired behavior is to reject unknown agents at the API layer, add validation.
RISK: HIGH

FILE: tests/test_api.py
CLASS: N/A (standalone async functions)
FUNCTION: test_create_instance_with_project_id
ASSERTION: POST /instances with {"agent_id": "coder", "project_id": "..."} calls spawn with agent_id="developer"
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Same as above. Update assertion to expect "coder".
RISK: HIGH

FILE: tests/test_api.py
CLASS: N/A (standalone async functions)
FUNCTION: test_stop_instance_deprecated_alias
ASSERTION: POST /instances/{id}/stop delegates to pause logic
NEEDS UPDATE: NO
UPDATE DESCRIPTION: This tests a deprecated endpoint alias (stop → pause), not the coder→developer alias. No dependency.
RISK: NONE
```

---

### FILE: `tests/conftest.py`

```
FILE: tests/conftest.py
CLASS: N/A (fixtures)
FUNCTION: sample_instance_create_data
ASSERTION: Returns {"agent_id": "coder"} fixture
NEEDS UPDATE: YES
UPDATE DESCRIPTION: This fixture is used by multiple test_classes in test_models.py to test alias normalization. After alias removal, the fixture value "coder" would no longer be normalized. The fixture itself can stay as-is (since it just provides data), but tests using it must update their assertions.
RISK: LOW (fixture data, not assertion)

FILE: tests/conftest.py
CLASS: N/A (fixtures)
FUNCTION: sample_instance_create_with_instance_id
ASSERTION: Returns {"agent_id": "coder", "instance_id": "custom-instance-123"} fixture
NEEDS UPDATE: YES
UPDATE DESCRIPTION: Same as above. Fixture can stay, tests must update.
RISK: LOW (fixture data, not assertion)
```

---

### FILE: `tests/unit/test_coder_agent.py`

**IMPORTANT**: This file tests the **standalone coder agent** in `agents/coder/`, NOT the alias. The alias shadows this agent, but the tests directly access the agent directory and metadata, bypassing alias resolution.

```
FILE: tests/unit/test_coder_agent.py
CLASS: TestCoderAutoDiscovery
FUNCTION: test_coder_directory_exists
ASSERTION: CODER_AGENT_DIR exists at agents/coder/
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests filesystem existence. Standalone coder agent exists and is unaffected by alias removal.
RISK: NONE

FILE: tests/unit/test_coder_agent.py
CLASS: TestCoderAutoDiscovery
FUNCTION: test_coder_not_in_skip_dirs
ASSERTION: "coder" not in SKIP_DIRS
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests registry skip list. Standalone coder is not in SKIP_DIRS. No dependency on AGENT_ID_ALIASES.
RISK: NONE

FILE: tests/unit/test_coder_agent.py
CLASS: TestCoderAutoDiscovery
FUNCTION: test_coder_discovered_in_registry
ASSERTION: registry.exists("coder") is True
NEEDS UPDATE: NO
UPDATE DESCRIPTION: After alias removal, registry.exists("coder") still returns True because the standalone coder agent is directly registered. Behavior unchanged.
RISK: NONE

FILE: tests/unit/test_coder_agent.py
CLASS: TestCoderAutoDiscovery
FUNCTION: test_coder_in_agent_list
ASSERTION: "coder" in agent_ids
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Standalone coder is in agent list. No change.
RISK: NONE

FILE: tests/unit/test_coder_agent.py
CLASS: TestCoderAutoDiscovery
FUNCTION: test_coder_metadata_loaded_correctly
ASSERTION: registry.get("coder").id == "coder"
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Tests standalone coder agent metadata. After alias removal, registry.get("coder") returns the standalone coder metadata (since alias no longer shadows it). This is actually CORRECT behavior. No change needed.
RISK: NONE
```

**Summary for test_coder_agent.py**: ALL 39 tests in this file are UNAFFECTED by alias removal. They test the standalone coder agent directly. The alias removal actually IMPROVES the situation by making `registry.get("coder")` return the correct standalone coder agent instead of the shadowed developer agent.

---

### FILE: `tests/test_spawn_instance_instructive_errors.py`

```
FILE: tests/test_spawn_instance_instructive_errors.py
CLASS: TestValidAgentId
FUNCTION: test_valid_agent_returns_tuple
ASSERTION: validate_agent_id("coder") returns ("developer", mock_path)
NEEDS UPDATE: YES
UPDATE DESCRIPTION: The test passes validate_agent_id("coder") and expects it to return "developer" (via alias). The mock_registry.resolve_pure_id.return_value = "developer" simulates the alias. After alias removal, the actual resolve_pure_id("coder") would return "coder" (standalone), and get("coder") would return the standalone coder's metadata. The test would need to either: (1) update mock to return "coder" and expect ("coder", mock_path), or (2) update the test description to clarify it's testing the alias behavior that's being removed.
RISK: HIGH
```

---

### FILE: `tests/test_loader.py`

```
FILE: tests/test_loader.py
CLASS: N/A
FUNCTION: N/A (setUp method comment)
ASSERTION: N/A
NEEDS UPDATE: NO
UPDATE DESCRIPTION: Line 840-844 contains a comment about alias-aware methods. This is a comment only, not an assertion. The comment documents WHY the mock stubs get_resolved and resolve_pure_id. After alias removal, the comment is still accurate (the loader still uses alias-aware methods, they just return the correct standalone coder). No functional change.
RISK: NONE (comment only)
```

---

### FILE: `tests/unit/test_wanderer_agent.py`

```
FILE: tests/unit/test_wanderer_agent.py
CLASS: N/A
FUNCTION: test_soul_mentions_future_coder_delegation_note
ASSERTION: wanderer/soul.md contains "coder→developer" or "coder->developer" and "future"
NEEDS UPDATE: CONDITIONAL
UPDATE DESCRIPTION: This is a documentation/content test for wanderer's soul.md. The test checks that the wanderer agent's soul.md documents the coder→developer alias. After alias removal, this documentation note becomes obsolete (or should be updated to reflect the new state). Options: (1) If the note is removed from soul.md as part of alias cleanup, remove this test. (2) If the note is kept (perhaps reworded to document the current state), update the assertion. (3) Change to assert that soul.md does NOT mention "coder→developer" (reflecting the removal).
RISK: MEDIUM — depends on what happens to the soul.md content during alias cleanup
```

---

### FILE: `tests/job_queue/test_status_alias_mapping.py`

```
FILE: tests/job_queue/test_status_alias_mapping.py
CLASS: TestNormalizeStatusesAliases, TestServiceListJobsWithAlias, etc.
FUNCTION: Multiple functions
ASSERTION: Job status aliases (running→processing, done→completed, waiting→pending) work correctly
NEEDS UPDATE: NO
UPDATE DESCRIPTION: These tests are about JOB STATUS aliases, not AGENT ID aliases. They test STATUS_ALIASES mapping job statuses like "running" → "processing". Completely unrelated to AGENT_ID_ALIASES.
RISK: NONE
```

---

## PART 4: Summary — Tests Needing Updates by Category

### HIGH RISK — Must update or remove (10 test functions + 1 fixture + 1 test class with 7 tests):

| File | Count | Reason |
|------|-------|--------|
| `tests/test_registry.py` | 7 | `TestAgentIdAliasBackwardCompatibility` — all 7 tests test alias behavior directly |
| `tests/unit/test_coder_developer_migration.py` | 4 | Part B tests mock `resolve_pure_id("coder")→"developer"` |
| `tests/test_spawn_team_members.py` | 3 | `test_alias_request_resolves_to_canonical_id`, `test_alias_request_canonicalizes`, `test_alias_caller_canonicalizes` |
| `tests/test_spawn_instance_validation.py` | 2 | Both tests assert `resolve_to_id("coder")=="developer"` |
| `tests/test_models.py` | 7 | All tests assert coder→developer normalization |
| `tests/test_api.py` | 2 | Both tests assert spawn called with "developer" not "coder" |
| `tests/test_spawn_instance_instructive_errors.py` | 1 | `test_valid_agent_returns_tuple` mocks alias behavior |
| `tests/conftest.py` | 2 fixtures | Provide `agent_id="coder"` data for alias tests |

### MEDIUM RISK — May need updates (2 tests):

| File | Count | Reason |
|------|-------|--------|
| `tests/unit/test_wanderer_agent.py` | 1 | Documentation test for soul.md content |
| `tests/test_spawn_team_members.py` | 1 | `test_alias_caller_resolves_to_canonical_id` — would pass but with different error message |

### LOW RISK — Comment updates only (4 tests):

| File | Count | Reason |
|------|-------|--------|
| `tests/test_models.py` | 4 | Obsolete comments about alias normalization |

### NO RISK — Unaffected (45+ tests):

| File | Count | Reason |
|------|-------|--------|
| `tests/unit/test_coder_agent.py` | ~39 | Tests standalone coder agent directly (no alias dependency) |
| `tests/unit/test_coder_developer_migration.py` | 5 | Part A tests the SQL migration (standalone) |
| `tests/test_spawn_team_members.py` | ~18 | Authorization and parsing tests (no alias) |
| `tests/job_queue/test_status_alias_mapping.py` | 15 | Job STATUS aliases, not agent ID aliases |
| Various other files | ~10+ | Non-alias tests |

---

## PART 5: Fixture Impact

Two fixtures in `tests/conftest.py` provide `agent_id="coder"` for test data:

- `sample_instance_create_data()` → `{"agent_id": "coder"}`
- `sample_instance_create_with_instance_id()` → `{"agent_id": "coder", "instance_id": "custom-instance-123"}`

These fixtures are used by:
- `tests/test_models.py::TestInstanceCreate::test_instance_create_required`
- `tests/test_models.py::TestInstanceCreate::test_instance_create_optional`
- `tests/test_models.py::TestInstanceCreate::test_instance_create_serialization`

After alias removal, the fixture data itself is still valid — it's just that "coder" would no longer be normalized. The fixtures don't need to change; the TEST ASSERTIONS need to change from expecting "developer" to expecting "coder".

---

## PART 6: Model Validator Impact

The `InstanceCreate.normalize_agent_id()` Pydantic validator in `daemon/models/instance.py:19-24` uses `AGENT_ID_ALIASES`:

```python
@field_validator("agent_id")
@classmethod
def normalize_agent_id(cls, v: str) -> str:
    """Normalize agent_id aliases (backward compat for renamed agents)."""
    from daemon.registry import AGENT_ID_ALIASES
    return AGENT_ID_ALIASES.get(v, v)
```

**When removing the alias, this validator MUST also be removed** (along with its import of `AGENT_ID_ALIASES`). This is a source code change, not a test change, but it directly causes test failures.

The following tests assert that this validator normalizes "coder" → "developer":
- `tests/test_registry.py::test_instance_create_normalizes_alias`
- `tests/test_models.py::TestInstanceCreate::*` (4 tests)
- `tests/test_models.py::TestModelValidation::*` (2 tests)
- `tests/test_api.py::test_create_instance_success`
- `tests/test_api.py::test_create_instance_with_project_id`

---

## PART 7: Files That Can Remain As-Is

The following files contain references to "coder" or "alias" but do NOT need updating:

| File | Reason |
|------|--------|
| `scripts/migrate_coder_to_developer.py` | Standalone SQL migration script, no alias dependency |
| `scripts/test-migration.sh` | Test script for the migration, uses dry-run |
| `daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql` | Standalone SQL migration file |
| `daemon/manager.py` | Contains migration SQL, but that's source code not tests |
| `tests/test_loader.py` | Only contains a comment about alias-aware methods, no assertions |
| `tests/job_queue/conftest.py` | Contains a comment about `sample_job_data_no_project_service` being an alias, unrelated |
| `tests/job_queue/test_idempotent_enqueue.py` | Only contains comments about alias-aware `get_resolved`, no assertions |
| `tests/job_queue/test_lock_repository.py` | Contains comment about `clear_stale_job_locks` alias for `clear_terminal_job_locks`, unrelated |

---

## PART 8: Total Update Count

| Category | Count |
|----------|-------|
| Tests needing complete rewrite/removal | **21** |
| Tests needing assertion updates only | **7** |
| Tests needing comment updates only | **4** |
| Fixtures needing no change (but tests change) | **2** |
| Tests fully unaffected | **~55** |
| **TOTAL TESTS ANALYZED** | **~89** |

---

## APPENDIX: The Standalone Coder Agent Context

The `agents/coder/` directory contains a **separate, distinct agent** from the original "coder → developer" rename:

- **Commit**: `a5a2b01a` — "feat: add standalone coder agent — direct coding without OpenCode"
- **Purpose**: A direct-coding agent that works hands-on with files, tests, and build systems WITHOUT OpenCode delegation
- **Meta.json**: `id: "coder"`, `name: "Coder"`, `innate_skills: ["todo", "chart"]`, `tools.allow: ["bash", "filesystem", "time", "self", "help", "knowledge", "context"]`
- **Different from developer**: The developer agent has `innate_skills: ["opencode", "chart", "todo"]` and `team_members: ["explorer"]`
- **Current state**: The alias shadows this agent. After alias removal, `resolve_pure_id("coder")` correctly returns "coder" (the standalone agent)

The alias was added before the standalone coder agent existed. The standalone coder was added later as a NEW agent, not as a rename of developer. The alias now incorrectly shadows it.

**Removing the alias would restore the correct behavior**: "coder" resolves to the standalone coder agent, "developer" resolves to the developer agent. They are separate, independent agents.

---

*Report generated: 2026-07-10*
*Investigation scope: scripts/migrate_coder_to_developer.py + ALL test files referencing coder/alias patterns*
