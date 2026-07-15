# Skill-Worker-Milestone Testing Notes
Date: 2026-07-15
Branch: feature/skill-worker-milestone

## Test Files & Counts
| File | Tests | Runtime |
|------|-------|---------|
| tests/unit/test_meta_tag_parsing.py | ~15 | <1s |
| tests/unit/test_finalize_on_replace.py | 10 | 0.86s |
| tests/unit/test_auto_load_metrics.py | ~7 | <1s |
| tests/unit/test_composite_scoring.py | ~14 | <1s |
| tests/unit/test_trigger_enhancements.py | ~9 | <1s |
| tests/repositories/test_skill_repository.py | 97 | 2.14s |
| tests/integration/test_skill_cross_phase_flow_b.py | 13 | 1.27s |
| tests/integration/test_skill_evolution_e2e.py | 24 | 3.37s |

## Gotchas
1. **Integration test marker override required**: `pyproject.toml` has `addopts = "-m 'not integration and not postgres'"`. Integration test files with `pytestmark = pytest.mark.integration` get deselected unless overridden with `-o "addopts="` or `-m integration`. This is expected project behavior.
2. **No commit hashes needed**: All 189 tests passed on first run — no quick fixes or code changes required.
3. **Content validation is static**: skill-set.md and workflow.md checks can be done directly by tester agent without delegation.

## Architecture Verified
- Meta-tag `<meta>{"load_skill": "name"}</meta>` parsing and extraction
- Finalize-on-replace with SUPERSEDED records
- Auto_load skill visibility in metrics
- Multi-metric composite A/B scoring (5 metrics, tie-break by challenger)
- Feedback-driven fallback (Option C): skill_feedback(applied=False) → fallback=True
- Security guards: REPLACE does NOT fire on completion/retry messages with meta tags
- Schema: ab_test_group + superseded columns + composite index + PG _ensure_postgres_columns
- Content: 1 auto_load=true (test-strategy), 8 auto_load=false, workflow.md has meta-tag pattern
