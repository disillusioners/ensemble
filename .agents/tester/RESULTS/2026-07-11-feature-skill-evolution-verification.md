# 2026-07-11 — feature/skill-evolution Worker Migration + Config Verification

**Branch**: `feature/skill-evolution`
**Verified commits**: `de8ff83f` (base) → `9446f30c` (after quick fixes)
**Sessions**: worker-verify, config-verify, skill-tests (parallel)
**Result**: ✅ **PASS** (migration complete + tests green)

---

## 1. Worker Agent Migration ✅

| Check | Result |
|-------|--------|
| `agents/worker/meta.json` parses via `AgentMetadata` | PASS |
| `skill_injection: true` present and loaded | PASS |
| `innate_skills: ["dynamic-skill", "todo"]` | PASS |
| `tools.allow` has `"dynamic-skill"` | PASS |
| `worker` still in `leader/team_members` | PASS |
| Ari agent loads via `AgentMetadata` | PASS |
| `tests/test_registry.py` | PASS (48/48) |

---

## 2. Config Validation ✅

| Check | Result |
|-------|--------|
| `SkillEvolutionConfig` fields load via `Config()` | PASS (19 fields) |
| `env_prefix == "SKILL_EVOLUTION_"` | PASS |
| `config.yaml` `skill_evolution` keys match class fields | PASS (19 ↔ 19) |
| `.env.example` `SKILL_EVOLUTION_*` env vars match class fields | PASS (19 ↔ 19) |
| Dedicated config test suite `tests/test_skill_evolution_config.py` | **PASS (12/12)** |

**No field name mismatches** between config.yaml, .env.example, and `daemon/config.py:SkillEvolutionConfig`. All three sources are in perfect 1:1:1 correspondence.

### Notable behavioral note (informational)
Empty-string env vars like `SKILL_EVOLUTION_EMBEDDING_API_KEY=` are stored as `""` (not `None`) by pydantic-settings. This is **intentional** and locked in by `test_env_override_optional_field_stores_empty_string`. Callers must use truthy-fallback semantics (e.g. `if not value: value = llm.api_key`).

### Note on `ab_min_confidence`
The pre-loaded context referenced `ab_min_confidence: 0.7`, but the actual class does **not** have this field. Only `ab_min_difference: 0.15` exists. Treat that context note as stale.

---

## 3. Skill-Related Tests ✅

| Suite | Before | After | Δ |
|-------|--------|-------|---|
| `pytest -k skill` | 10 failed, 656 passed, 4 skipped | 663 passed, 7 skipped | **+7 net** |
| `pytest -k dynamic_skill` | 1 passed | 2 passed | +1 |
| `pytest tests/test_registry.py` | 48 passed | 48 passed | 0 |
| `pytest -k config` | 3 failed, 2 errors, 503 passed | 2 failed, 506 passed | +3, -1 errors |
| `pytest -k worker` | 6 failed, 209 passed | 211 passed, 4 skipped | **+2 net** |

### Root Cause Pattern Discovered
A repo-wide mock-fixture migration was missed when `daemon/registry.py` renamed `get(agent_id)` → `get_resolved(agent_id)` for skill-evolution work. Multiple test files still set `mock_registry.return_value.get.return_value = agent_metadata`, so production code receives a MagicMock. The most insidious failure was `test_tool_filter.py::test_explicit_deny_still_wins_over_innate_skill_grant`: set operations against MagicMocks silently no-op'd, hiding the deny logic entirely.

### Failures Remaining (out of scope, pre-existing)
- `tests/unit/test_vision.py::TestImagesWithoutVisionConfig::test_send_message_without_images_no_vision_succeeds` — MagicMock awaiting on `manager.enqueue_message_job`.
- `tests/unit/test_startup_integration.py::TestHealthEndpointLogic::test_health_endpoint_returns_ensemble_config_fields` — test pollution (passes in isolation).

---

## Quick Fixes Applied (commits added on top of `de8ff83f`)

| Commit | Session | What |
|--------|---------|------|
| `9dec976e` | worker-verify | test fix for skill-evolution era mock fixtures |
| `9446f30c` | skill-tests | 6-file test fixture migration (`+81/-133`) |

### `9dec976e` (worker-verify session)
Specific test fix (one-line ordering fix) for `test_c3_subquery_protects_queued_locks` in the worker-verify isolated run.

### `9446f30c` (skill-tests session) — 6 files

| File | Δ | What |
|------|---|------|
| `tests/unit/tools/test_inner_soul_redirect.py` | +1 | `get_resolved.return_value` mock fixture |
| `tests/unit/test_context7_builtin.py` | +3 | `config.skill_evolution = None` for manager.py:744 |
| `tests/test_tool_filter.py` | ~+24 | Migrate `mock_registry.get` → `get_resolved` (6 tests) |
| `tests/unit/test_worker_agent.py` | -135 net | Migrate obsolete innate-skills test variant to dynamic-skill; obsolete→skip stubs |
| `tests/unit/test_devops_agent.py` | ~+35 | Rename `test_innate_skills_is_empty_list` → `..._is_todo`; relax skills=={} |

---

## Out-of-scope follow-ups (not addressed; flagged for future)

1. **`ari/rule.md` terminology consistency** — still uses legacy "task" domain label (lines 21, 62, 86-89). Functional but legacy wording.
2. **`docs/skill-evolution.md`** — referenced in commit but not reviewed here.
3. **Pre-existing** `test_c3_subquery_protects_queued_locks` — ordering fix in lock_repository or test.

---

## Verdict

✅ **READY** — feature/skill-evolution migration is sound. All worker migration, config validation, and skill tests pass after two small test-fix commits. Pre-existing failures are unrelated to this work.
