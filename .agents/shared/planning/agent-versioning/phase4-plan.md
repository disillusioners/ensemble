# Phase 4: Testing & Backward Compatibility

## Objective

Comprehensive test suite covering the full versioning stack: registry tag parsing, API endpoints, DB persistence, and backward compatibility regression. Ensure all 23 existing agents continue to work unchanged and that the system handles edge cases gracefully.

## Coupling

- **Depends on**: Phases 1–3 (all functionality must be implemented)
- **Coupling type**: independent (test layer)
- **Shared files with other phases**: Test files reference all implementation files
- **Shared APIs/interfaces**: Tests verify the contracts defined in Phases 1–3
- **Why this coupling**: Tests validate the complete feature; can begin drafting during Phase 3 but must run after all implementation.

## Context

- Previous phases delivered: Registry parsing (P1), API + DB (P2), Frontend (P3)
- Key decisions: All decisions validated through tests

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Registry unit tests (Phase 1 coverage) | Tests for `_parse_agent_dir_name()` regex (incl. tightened charset), `discover()` with tagged dirs, `get_version()` fallback chain, `list_versions()`, `list_all_grouped()`. **D16 keystone invariant**: resolve_* never returns composite keys. | `tests/test_registry_versioning.py` |
| 2 | API integration tests (Phase 2 coverage) | Tests for `GET /api/agents` with version info, `POST /api/instances` with `version_tag`, DB persistence of `agent_tag`, instance info response. **C2: invalid version_tag raises ValueError**. **Approver #4: path-form agent_id resolves correctly**. | `tests/test_agent_versioning_api.py` |
| 3 | **Instance lifecycle tests (C1 + D15 coverage — CRITICAL)** | Tests for `_restore_instance()` loading correct tagged version. **S3: simulate daemon restart with a running tagged instance**. **D15: PromptCache isolation — base and tagged don't collide**. | `tests/test_agent_versioning_lifecycle.py` (new) |
| 4 | DB migration tests | Verify SQLite migration creates `agent_tag` column, PostgreSQL `_ensure_postgres_columns()` is idempotent, existing rows have NULL `agent_tag`. | `tests/test_agent_tag_migration.py` (new) |
| 5 | **Router skip-rule audit test (C4)** | Test asserting the refactored `GET /api/agents` produces identical agent list to pre-refactor behavior for the 23 existing agents. Verify `_`-prefixed dirs are excluded. | `tests/test_agent_versioning_api.py` |
| 6 | Backward compatibility regression suite | Run existing test suite (registry tests, instance creation tests, agent router tests) to verify zero regressions. Specifically: 23 existing agents load, `get()` returns base, `resolve_to_id()` unchanged, `POST /instances` without `version_tag` works. | Existing test files |
| 7 | Edge case tests | Nested brackets `dev[[v2]]`, non-trailing `dev[v2]x`, empty tag `dev[]`, **path traversal `dev[../etc]`** (tightened regex rejects), agent with only tagged version (no base), duplicate tags, special chars in tags. | `tests/test_registry_versioning.py` |
| 8 | Frontend unit tests (Phase 3 coverage) | VersionPickerComponent tests, agent deduplication (W8 tiebreaker), **all 5 call sites send version_tag (C3)**, createInstance sends version_tag in body, picker hidden for single-version. | Component `.spec.ts` files |
| 9 | End-to-end manual test checklist | Manual verification including **S3: daemon restart with tagged instance** and **D15: PromptCache isolation**. See checklist below. | `.agents/shared/planning/agent-versioning/e2e-checklist.md` |
| 10 | Cross-database verification | Run tests against BOTH SQLite and PostgreSQL. Verify migration applies cleanly on existing PG database (not just fresh). | Test runner config |

## Key Files

- `tests/test_registry_versioning.py` — Registry tests (Phase 1)
- `tests/test_agent_versioning_api.py` — API integration tests (Phase 2)
- `tests/test_agent_tag_migration.py` — DB migration tests
- Various frontend `.spec.ts` files (Phase 3)

## Test Categories

### Registry Unit Tests (test_registry_versioning.py)

```python
class TestTagParsing:
    """Test _parse_agent_dir_name() regex with tightened charset."""
    
    def test_untagged_dir(self):
        assert _parse_agent_dir_name("developer") == ("developer", None)
    
    def test_tagged_dir(self):
        assert _parse_agent_dir_name("developer[v2]") == ("developer", "v2")
    
    def test_multi_word_tag(self):
        assert _parse_agent_dir_name("dev[test-version]") == ("dev", "test-version")
    
    def test_nested_brackets_no_match(self):
        assert _parse_agent_dir_name("dev[[v2]]") == ("dev[[v2]]", None)
    
    def test_non_trailing_bracket_no_match(self):
        assert _parse_agent_dir_name("dev[v2]extra") == ("dev[v2]extra", None)
    
    def test_empty_tag_no_match(self):
        assert _parse_agent_dir_name("dev[]") == ("dev[]", None)
    
    def test_leading_bracket_no_match(self):
        assert _parse_agent_dir_name("[v2]developer") == ("[v2]developer", None)
    
    def test_path_traversal_in_tag_rejected(self):
        """Tightened regex rejects /, \\, .. in tags."""
        assert _parse_agent_dir_name("dev[../etc]") == ("dev[../etc]", None)
        assert _parse_agent_dir_name("dev[v2/sub]") == ("dev[v2/sub]", None)


class TestDiscoverWithVersions:
    """Test discover() with mixed tagged/untagged directories."""
    
    def test_base_and_tagged_discovered(self, tmp_path):
        """developer/ and developer[v2]/ both discovered under agent_id 'developer'."""
    
    def test_only_tagged_no_base(self, tmp_path):
        """developer[v2]/ exists but no developer/ — _versions has ['v2'], no None."""
    
    def test_multiple_tags(self, tmp_path):
        """developer/, developer[v2]/, developer[test]/ → versions [None, 'v2', 'test']."""
    
    def test_backward_compat_untagged_only(self, tmp_path):
        """No tagged dirs — behavior identical to current."""


class TestGetVersion:
    """Test get_version() fallback chain (decisions.md D8)."""
    
    def test_explicit_tag(self):
        """get_version('developer', 'v2') returns v2 metadata."""
    
    def test_none_tag_with_base(self):
        """get_version('developer', None) returns base metadata."""
    
    def test_none_tag_no_base(self):
        """get_version('custom', None) with only custom[v1]/ returns v1 metadata."""
    
    def test_nonexistent_version(self):
        """get_version('developer', 'nonexistent') returns None."""
    
    def test_nonexistent_agent(self):
        """get_version('ghost', None) returns None."""


class TestResolverInvariant:
    """D16 keystone invariant: resolve_* never returns composite keys."""
    
    def test_resolve_pure_id_rejects_composite(self):
        """resolve_pure_id('developer[v2]') must return None — not a composite key."""
    
    def test_resolve_to_id_rejects_composite_path(self):
        """resolve_to_id('./agents/developer[v2]') must not return 'developer[v2]'."""
    
    def test_list_all_has_no_composite_ids(self):
        """No entry in list_all() should contain '[' in its id."""
    
    def test_find_skill_returns_base_ids(self):
        """find_skill() returns base agent_ids, never composite keys."""


class TestPromptCacheIsolation:
    """D15: Base and tagged versions must have separate cache entries."""
    
    def test_base_and_tagged_get_different_cache_keys(self):
        """_make_key('developer', None, None) != _make_key('developer', None, 'v2')."""
    
    def test_base_prompt_not_served_to_tagged_instance(self):
        """Loading base prompt then v2 prompt should NOT return cached base."""


class TestBackwardCompat:
    """Verify existing methods unchanged."""
    
    def test_get_returns_base(self):
        """get('developer') returns base version (version_tag=None)."""
    
    def test_resolve_to_id_unchanged(self):
        """resolve_to_id('developer') returns 'developer'."""
    
    def test_list_all_returns_base_only(self):
        """list_all() returns only base entries — no duplicates from _versioned_agents."""
```

### API Integration Tests (test_agent_versioning_api.py)

```python
class TestAgentListWithVersions:
    
    async def test_api_returns_version_tag(self, client):
        """GET /api/agents includes version_tag field."""
    
    async def test_api_returns_available_versions(self, client):
        """GET /api/agents includes available_versions list."""
    
    async def test_api_groups_versions_by_id(self, client):
        """developer and developer[v2] both have id='developer'."""
    
    async def test_router_skips_underscore_dirs(self, client):
        """C4: Refactored router still excludes _-prefixed dirs."""


class TestInstanceCreationWithVersion:
    
    async def test_create_with_version_tag(self, client):
        """POST /instances with version_tag resolves correct agent_dir."""
    
    async def test_create_without_version_tag_backward_compat(self, client):
        """POST /instances without version_tag uses base (existing behavior)."""
    
    async def test_agent_tag_persisted(self, client, db):
        """Instance row has agent_tag column populated."""
    
    async def test_agent_tag_null_when_no_version(self, client, db):
        """Instance row has agent_tag=NULL when no version_tag provided."""
    
    async def test_invalid_version_tag_raises_error(self, client):
        """C2: POST /instances with version_tag='nonexistent' returns 400,
        does NOT silently fall back to base."""


class TestInstanceRestoreWithVersion:
    """C1: Verify _restore_instance loads correct tagged version."""
    
    def test_restore_loads_tagged_prompt(self, lifecycle_service, db):
        """S3: Instance with agent_tag='v2' restores from agents/developer[v2]/ path."""
    
    def test_restore_falls_back_for_legacy_instances(self, lifecycle_service, db):
        """Instance with agent_tag=NULL (legacy) restores from base path."""
    
    def test_restart_simulates_correct_version(self, lifecycle_service, db):
        """S3: Full restart simulation — tagged instance gets tagged prompt."""
```

### DB Migration Tests (test_agent_tag_migration.py)

```python
class TestAgentTagMigration:
    
    def test_sqlite_migration_adds_column(self, sqlite_engine):
        """Running migration adds agent_tag column to instances."""
    
    def test_postgres_ensure_columns_idempotent(self, pg_engine):
        """_ensure_postgres_columns() with agent_tag is idempotent."""
    
    def test_existing_rows_have_null_tag(self, existing_db):
        """Pre-existing instance rows have agent_tag=NULL."""
    
    def test_no_index_created(self, db):
        """W9: No index on agent_tag (queries filtering by tag are rare)."""
```

## Constraints

- Tests MUST run against both SQLite and PostgreSQL (PostgreSQL is PRIMARY).
- No SQLite-only syntax in test SQL.
- Backward compat tests MUST pass without any code changes to existing test expectations.
- Edge case tests for tag parsing must cover all regex boundary conditions.

## Success Criteria (E2E Checklist)

- [ ] Create `agents/developer[v2]/` with valid meta.json
- [ ] Restart daemon → no errors in logs
- [ ] `GET /api/agents` shows developer with `available_versions: [null, "v2"]`
- [ ] Frontend agent selector shows version picker for developer
- [ ] Select "v2" → create instance → instance uses `agents/developer[v2]/` path
- [ ] DB row has `agent_tag = "v2"`
- [ ] **D15: Create a base developer instance first, then a v2 instance — v2 gets v2 prompt, NOT cached base prompt**
- [ ] **C2: Try creating instance with `version_tag = "v3"` (typo) → get 400 error, NOT base agent**
- [ ] Create instance without selecting version → uses base `agents/developer/`
- [ ] DB row has `agent_tag = NULL`
- [ ] **S3: With a running `developer[v2]` instance, restart daemon → instance restores with v2 prompt (not base)**
- [ ] All 23 existing agents still listed and functional
- [ ] `resolve_to_id("developer")` still returns `"developer"`
- [ ] **D16: `resolve_to_id("developer[v2]")` returns None or "developer" — NEVER `"developer[v2]"`**
- [ ] Existing instance with no `agent_tag` loads and runs correctly
- [ ] **C4: Agent list from refactored router matches pre-refactor list for 23 existing agents**
- [ ] **C5: `list_all()` returns exactly 23 entries (no duplicates from tagged dirs)**
- [ ] **Tightened regex: `agents/dev[../etc]/` is NOT parsed as a version tag**

## Deliverables

- [ ] Registry unit tests (tag parsing incl. tightened charset, discovery, get_version, backward compat)
- [ ] **D16 resolver invariant tests (resolve_* never returns composite keys)**
- [ ] **D15 PromptCache isolation tests (base/tagged don't collide)**
- [ ] API integration tests (version surfacing, instance creation, DB persistence, **C2 error on invalid tag**, **path-form agent_id**)
- [ ] **Instance lifecycle tests (C1 restore loads correct version, S3 restart simulation, D15 cache isolation)**
- [ ] DB migration tests (SQLite + PostgreSQL, idempotency, **no index per W9**)
- [ ] **Router skip-rule audit test (C4 — identical agent list)**
- [ ] Backward compatibility regression passes (zero failures in existing suite)
- [ ] Edge case tests (nested brackets, path traversal rejection, missing base, duplicate tags)
- [ ] Frontend unit tests (VersionPicker, deduplication W8, **all 5 call sites C3**, API calls)
- [ ] E2E manual checklist documented and verified (including S3 restart + D15 cache tests)
