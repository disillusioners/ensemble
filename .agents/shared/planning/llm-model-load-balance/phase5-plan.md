# Phase 5: Testing Strategy

## Objective

Provide complete test coverage for the LLM model load-balancing feature across five dimensions: (1) algorithmic correctness of the weighted selector, (2) meta.json loading survives the `extra='ignore'` C6 trap, (3) priority ordering at the integration boundary, (4) DB persistence and restore, (5) backward compatibility. All tests must pass against PostgreSQL (the primary dev/test DB).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `tests/test_llm_load_balance.py` with unit tests for `_select_weighted_model` | Phase 2 | All edge-case tests pass; statistical distribution test passes (50k samples within ±2% of expected) |
| 2 | Add C6 regression test: load real meta.json with `llm_models`, assert field survives | Phase 1 | New test in `tests/test_llm_load_balance_meta_loading.py` (or merged into existing governor_integration test file); follows `tests/test_governor_integration.py:229-274` pattern |
| 3 | Add integration tests for `_build_llm_config` priority ordering | Phase 3 | New tests in `tests/test_llm_load_balance_integration.py`: override > load-balance > llm_model > default; each priority verified in isolation |
| 4 | Add integration test for DB persistence (spawn → inspect DB row → verify `model_override`) | Phase 4 | Test against PostgreSQL (per "PostgreSQL is the PRIMARY dev/test DB" constraint). Verify `model_override` populated with the resolved model. |
| 5 | Add integration test for restore: spawn → simulate restart → restore → model unchanged | Phase 4 | Test passes: model survives daemon restart |
| 6 | Add backward-compatibility tests (no `llm_models`, empty `llm_models`, single entry) | Phase 1, 2, 3 | All existing tests still pass; new backward-compat tests pass; no behavioral regression |
| 7 | Add allowed-models filtering tests (mixed valid/invalid, all-invalid → fallback) | Phase 2, 3 | Filtering works; all-invalid falls back to `llm_model` |
| 8 | Add a final smoke test that exercises the entire path through the HTTP API | Phase 1, 2, 3, 4 | End-to-end test: POST /agents/.../spawn → response includes correct `model`; concurrent spawns distribute across the pool |
| 9 | Add spawn-path coverage tests: spawn_instance_with_mcp, invoke_agent_and_wait, explorer caller_model_overrides, default/fallback paths | Phase 3 | New tests verify that each spawn path correctly bypasses or triggers load balancing; see Task 9 code sketch |

## Coupling

- **Independent** of Phases 1-4 in terms of "depends on" — Phase 5 runs *after* all four, but the tests can be **drafted in parallel** with those phases.
- **Verifies** Phases 1-4 — failure of any test indicates which phase regressed.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Statistical test is flaky (CI variance on 10k samples) | Medium | Medium | Use seed (`random.seed(42)`) for reproducibility; 50k samples with ±2% tolerance balances flakiness and sensitivity. If still flaky in CI, increase to 100k samples. |
| Tests pass on SQLite but fail on PostgreSQL (JSONB behavior) | Medium | Low | Per critical-notes constraint: run tests against PostgreSQL. Verify JSONB serialization in Task 4. |
| C6 test misses a path (e.g., versioned agents) | Low | Low | Test both base and versioned agent paths (per `daemon/registry.py:398-408` two storage paths) |
| Restore test is slow / flaky in CI | Medium | Medium | Use in-memory DB or temporary PostgreSQL schema for fast teardown; isolate from other concurrent tests |
| Test fixtures leak between tests (random state) | Low | Medium | Each test sets `random.seed(0)` in setup; or each test uses its own RNG instance |

## Code Sketch

### Task 1: Unit tests for `_select_weighted_model`

Location: `tests/test_llm_load_balance.py` (new file)

```python
"""Unit tests for the weighted random model selection algorithm."""
import random
import pytest
from daemon.services.llm_load_balancer import _select_weighted_model, LLMModelWeight


def make_pool(*pairs: tuple[str, int]) -> list[LLMModelWeight]:
    return [LLMModelWeight(model=m, weight=w) for m, w in pairs]


class TestSelectWeightedModel:
    # --- Edge cases ---

    def test_none_input_returns_none(self):
        assert _select_weighted_model(None, []) is None

    def test_empty_list_returns_none(self):
        assert _select_weighted_model([], []) is None

    def test_single_entry_always_selected(self):
        pool = make_pool(("m1", 1))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    # --- Statistical correctness ---

    def test_equal_weights_distribution(self):
        random.seed(42)
        pool = make_pool(("m1", 50), ("m2", 50))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        assert 24000 <= counts["m1"] <= 26000, f"m1 count {counts['m1']} outside ±2%"
        assert 24000 <= counts["m2"] <= 26000, f"m2 count {counts['m2']} outside ±2%"

    def test_heavy_weight_distribution(self):
        random.seed(42)
        pool = make_pool(("m1", 90), ("m2", 10))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        assert 44000 <= counts["m1"] <= 46000, f"m1 count {counts['m1']} outside ±2%"
        assert 4000 <= counts["m2"] <= 6000, f"m2 count {counts['m2']} outside ±2%"

    # --- Weight clamping ---

    def test_clamp_low(self):
        pool = make_pool(("m1", 0))
        for _ in range(100):
            assert _select_weighted_model(pool, []) == "m1"

    def test_clamp_high(self):
        pool = make_pool(("m1", 200))
        for _ in range(100):
            assert _select_weighted_model(pool, []) == "m1"

    def test_clamp_negative(self):
        random.seed(42)
        pool = make_pool(("m1", -5), ("m2", 50))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # -5 → 1, 50 → 50. Ratio ~1/51 ≈ 2% for m1, ~98% for m2
        assert counts["m1"] < 1500, f"m1 count {counts['m1']} too high (expected <3%)"
        assert counts["m2"] > 48500

    # --- Allowed-models filtering ---

    def test_all_filtered_returns_none(self):
        pool = make_pool(("m1", 100))
        assert _select_weighted_model(pool, ["m_other"]) is None

    def test_mixed_filter(self):
        random.seed(42)
        pool = make_pool(("m1", 50), ("m_blocked", 100), ("m2", 50))
        allowed = ["m1", "m2"]
        for _ in range(1000):
            result = _select_weighted_model(pool, allowed)
            assert result in ("m1", "m2"), f"Got filtered model: {result}"

    def test_case_insensitive_filter(self):
        pool = make_pool(("ModelA", 1))
        assert _select_weighted_model(pool, ["modela"]) == "ModelA"
        assert _select_weighted_model(pool, ["MODELA"]) == "ModelA"

    # --- Duplicates and edge inputs ---

    def test_duplicate_models_are_additive(self):
        pool = make_pool(("m1", 50), ("m1", 50))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    def test_whitespace_model_skipped(self):
        pool = make_pool(("", 100), ("m1", 1))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    def test_whitespace_only_model_skipped(self):
        pool = make_pool(("   ", 100), ("m1", 1))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    def test_all_whitespace_returns_none(self):
        pool = make_pool(("", 100), ("   ", 100))
        assert _select_weighted_model(pool, []) is None

    def test_empty_allowed_models_no_restriction(self):
        random.seed(42)
        pool = make_pool(("m1", 1), ("m2", 1))
        seen = set()
        for _ in range(100):
            seen.add(_select_weighted_model(pool, []))
        assert seen == {"m1", "m2"}

    def test_none_allowed_models_no_restriction(self):
        random.seed(42)
        pool = make_pool(("m1", 1), ("m2", 1))
        seen = set()
        for _ in range(100):
            seen.add(_select_weighted_model(pool, None))
        assert seen == {"m1", "m2"}
```

### Task 2: C6 regression test

Location: `tests/test_llm_load_balance_meta_loading.py` (new file) — or add to existing `tests/test_governor_integration.py`

```python
"""C6 regression: llm_models survives meta.json loading despite extra='ignore'."""
import pytest
from pathlib import Path
from daemon.registry import AgentRegistry


@pytest.fixture
def test_agent_dir(tmp_path) -> Path:
    """Create a minimal agents/ dir with one agent declaring llm_models."""
    agent_path = tmp_path / "agents" / "load_balance_test_agent"
    agent_path.mkdir(parents=True)
    meta = {
        "soul": "test",
        "llm_models": [
            {"model": "gpt-4o", "weight": 70},
            {"model": "claude-sonnet-4", "weight": 30},
        ],
    }
    (agent_path / "meta.json").write_text(__import__("json").dumps(meta))
    return tmp_path


def test_llm_models_survives_loading(test_agent_dir):
    registry = AgentRegistry(test_agent_dir / "agents")
    registry.discover()
    agent = registry.get("load_balance_test_agent")
    assert agent is not None
    assert agent.llm_models is not None, "C6 REGRESSION: llm_models silently dropped"
    assert len(agent.llm_models) == 2
    assert agent.llm_models[0].model == "gpt-4o"
    assert agent.llm_models[0].weight == 70
    assert agent.llm_models[1].model == "claude-sonnet-4"
    assert agent.llm_models[1].weight == 30


def test_llm_models_absent_returns_none(test_agent_dir):
    """Backward compat: agents without llm_models still load fine."""
    agent_path = test_agent_dir / "agents" / "no_load_balance"
    agent_path.mkdir(parents=True)
    (agent_path / "meta.json").write_text('{"soul": "test"}')
    registry = AgentRegistry(test_agent_dir / "agents")
    registry.discover()
    agent = registry.get("no_load_balance")
    assert agent is not None
    assert agent.llm_models is None


def test_llm_models_empty_array_loads_as_empty_list(test_agent_dir):
    """Backward compat: empty llm_models array does not crash."""
    agent_path = test_agent_dir / "agents" / "empty_pool"
    agent_path.mkdir(parents=True)
    (agent_path / "meta.json").write_text('{"soul": "test", "llm_models": []}')
    registry = AgentRegistry(test_agent_dir / "agents")
    registry.discover()
    agent = registry.get("empty_pool")
    assert agent is not None
    assert agent.llm_models == []


def test_malformed_llm_models_falls_back_gracefully(test_agent_dir):
    """Malformed llm_models entries must not crash agent discovery."""
    agent_path = test_agent_dir / "agents" / "broken_pool"
    agent_path.mkdir(parents=True)
    (agent_path / "meta.json").write_text(
        '{"soul": "test", "llm_models": [{"weight": 50}]}'  # missing 'model'
    )
    registry = AgentRegistry(test_agent_dir / "agents")
    registry.discover()  # must not raise
    agent = registry.get("broken_pool")
    assert agent is not None  # agent still loads
    assert agent.llm_models is None  # but llm_models is dropped
```

### Task 3: Integration tests for `_build_llm_config` priority

Location: `tests/test_llm_load_balance_integration.py` (new file)

```python
"""Integration tests for _build_llm_config priority ordering."""
import pytest
from unittest.mock import MagicMock
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.llm_load_balancer import LLMModelWeight
from daemon.registry import AgentMetadata


@pytest.fixture
def lifecycle():
    config = MagicMock()
    config.llm.model = "default-model"
    config.llm.allowed_models = []
    config.llm.llm_config_base = {"temperature": 0.7}
    return InstanceLifecycleService(config)


class TestBuildLlmConfigPriority:
    """Priority order (highest to lowest):
    1. override_model
    2. llm_models weighted random
    3. llm_model
    4. default
    """

    def test_default_when_no_metadata(self, lifecycle):
        cfg = lifecycle._build_llm_config(None)
        assert cfg["model"] == "default-model"

    def test_llm_model_overrides_default(self, lifecycle):
        meta = AgentMetadata(llm_model="agent-model")
        cfg = lifecycle._build_llm_config(meta)
        assert cfg["model"] == "agent-model"

    def test_llm_models_overrides_llm_model(self, lifecycle):
        meta = AgentMetadata(
            llm_model="agent-model",
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        cfg = lifecycle._build_llm_config(meta)
        assert cfg["model"] == "pool-model"

    def test_override_model_wins_over_everything(self, lifecycle):
        meta = AgentMetadata(
            llm_model="agent-model",
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        cfg = lifecycle._build_llm_config(meta, override_model="forced-model")
        assert cfg["model"] == "forced-model"

    def test_llm_models_skipped_when_override_set(self, lifecycle):
        """Load balancing must NOT fire when override is present."""
        meta = AgentMetadata(
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        # Run 100 times — if load balancing fired, we'd sometimes get "pool-model"
        for _ in range(100):
            cfg = lifecycle._build_llm_config(meta, override_model="forced-model")
            assert cfg["model"] == "forced-model"

    def test_empty_llm_models_falls_back_to_llm_model(self, lifecycle):
        meta = AgentMetadata(llm_model="agent-model", llm_models=[])
        cfg = lifecycle._build_llm_config(meta)
        assert cfg["model"] == "agent-model"

    def test_resolved_model_out_param(self, lifecycle):
        meta = AgentMetadata(llm_model="agent-model")
        holder: list[str] = []
        cfg = lifecycle._build_llm_config(meta, resolved_model_out=holder)
        assert holder == ["agent-model"]
        assert cfg["model"] == "agent-model"
```

### Task 4: DB persistence integration test

Location: `tests/test_llm_load_balance_persistence.py` (new file) — runs against PostgreSQL

> **Note on fixtures:** The real `spawn_instance(agent_id=..., model=...)` API does NOT accept a `metadata=` parameter. Instead, register the test agent in the registry BEFORE calling `spawn_instance`. The `test_agent_with_llm_models` fixture creates a temporary agent directory with the desired `llm_models` in `meta.json`, calls `registry.discover()`, and yields the agent_id. The `test_agent_fixture` variant registers a plain agent (no `llm_models`) for baseline tests.

```python
"""Integration tests for DB persistence of the resolved model."""
import pytest
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.llm_load_balancer import LLMModelWeight
from daemon.registry import AgentMetadata


@pytest.mark.asyncio
async def test_load_balanced_model_persisted_to_db(postgres_db, lifecycle_with_real_db, test_agent_with_llm_models):
    """Agent 'lb_test' is pre-registered in the registry with
    llm_models=[{m1,1},{m2,1}] via the test_agent_with_llm_models fixture."""
    instance = await lifecycle_with_real_db.spawn_instance(
        agent_id="lb_test",  # fixture registers this agent with llm_models
        model=None,  # no override — triggers load balancing
    )
    db_row = await postgres_db.fetch_one(
        "SELECT instance_metadata FROM instances WHERE instance_id = $1",
        instance[0] if isinstance(instance, tuple) else instance.id,
    )
    persisted_model = db_row["instance_metadata"].get("model_override")
    assert persisted_model in ("m1", "m2")


@pytest.mark.asyncio
async def test_override_model_persisted(postgres_db, lifecycle_with_real_db, test_agent_fixture):
    """Agent 'lb_test' is pre-registered in the registry via fixture."""
    instance = await lifecycle_with_real_db.spawn_instance(
        agent_id="lb_test",  # fixture registers this agent
        model="forced",  # explicit override — highest priority, skips load balancing
    )
    instance_id = instance[0] if isinstance(instance, tuple) else instance.id
    db_row = await postgres_db.fetch_one(
        "SELECT instance_metadata FROM instances WHERE instance_id = $1",
        instance_id,
    )
    assert db_row["instance_metadata"]["model_override"] == "forced"
```

### Task 5: Restore integration test

Location: `tests/test_llm_load_balance_restore.py` (new file)

```python
"""Verify selected model survives daemon restart (crash recovery)."""
import pytest


@pytest.mark.asyncio
async def test_load_balanced_model_survives_restart(postgres_db, daemon_factory, test_agent_with_llm_models):
    """Agent 'lb_test' is pre-registered in the registry with
    llm_models=[{m1,50},{m2,50}] via the test_agent_with_llm_models fixture."""
    # 1. Spawn with llm_models and capture the selected model
    initial_daemon = await daemon_factory()
    result = await initial_daemon.spawn_instance(
        agent_id="lb_test",  # fixture registers this agent with llm_models
        model=None,  # no override — triggers load balancing
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    original_model = await initial_daemon.get_instance_model(instance_id)
    assert original_model in ("m1", "m2")

    # 2. Kill daemon (simulate crash)
    await initial_daemon.shutdown()

    # 3. Restart daemon (same DB)
    restarted_daemon = await daemon_factory()
    restored_model = await restarted_daemon.get_instance_model(instance_id)

    # 4. Model unchanged
    assert restored_model == original_model, (
        f"Model changed after restart: {original_model} -> {restored_model}. "
        "Persistence or restore path broken."
    )
```

### Task 6: Backward compatibility tests

Covered in Task 2 above (`test_llm_models_absent_returns_none`, `test_llm_models_empty_array_loads_as_empty_list`). Plus run the full pre-existing test suite to confirm no regression.

### Task 7: Allowed-models filtering integration tests

```python
class TestAllowedModelsIntegration:
    def test_filtered_model_not_in_distribution(self, lifecycle_with_allowed):
        """When llm_models contains models outside allowed, those are excluded."""
        # Set up agent with llm_models=[m_allowed, m_blocked] and allowed_models=[m_allowed]
        # Run spawn 100 times, assert resolved model is always m_allowed
        ...

    def test_all_filtered_falls_back_to_llm_model(self, lifecycle_with_allowed):
        """When all llm_models are filtered out, llm_model is used."""
        # Set up agent with llm_models=[m_blocked] (all filtered) and llm_model="fallback"
        # Assert resolved model == "fallback"
        ...
```

### Task 8: End-to-end smoke test

```python
@pytest.mark.asyncio
async def test_http_spawn_response_includes_model(http_client, test_agent_meta):
    response = await http_client.post(
        "/api/instances/spawn",
        json={"agent_id": "lb_test_agent"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "model" in body
    assert body["model"] in ("m1", "m2")


@pytest.mark.asyncio
async def test_concurrent_spawns_distribute_across_pool(http_client, test_agent_meta):
    """Spawn 100 instances concurrently, verify distribution is ~uniform."""
    models = []
    for _ in range(100):
        response = await http_client.post(
            "/api/instances/spawn",
            json={"agent_id": "lb_test_agent"},
        )
        models.append(response.json()["model"])
    counts = {m: models.count(m) for m in set(models)}
    # 50/50 weights, 100 samples → expect ~50 each, ±20% tolerance
    for m, count in counts.items():
        assert 30 <= count <= 70, f"Distribution skewed: {counts}"
```

### Task 9: Spawn-path coverage tests

Location: `tests/test_llm_load_balance_spawn_paths.py` (new file)

These tests verify that ALL spawn paths correctly interact with load balancing.
Every spawn path converges on `manager.spawn_instance(model=...)` — if `model`
is explicitly set, load balancing is bypassed. If `model=None`, load balancing fires.

```python
"""Tests verifying that each spawn path correctly bypasses or triggers load balancing."""
import pytest


@pytest.mark.asyncio
async def test_spawn_instance_with_explicit_model_bypasses_lb(
    daemon_factory, test_agent_with_llm_models
):
    """spawn_instance(agent_id=..., model='forced') → no load balancing."""
    daemon = await daemon_factory()
    result = await daemon.spawn_instance(
        agent_id="lb_test",  # agent has llm_models=[{m1,50},{m2,50}]
        model="forced",  # explicit override → highest priority
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    assert model == "forced"  # not m1 or m2


@pytest.mark.asyncio
async def test_spawn_instance_no_model_triggers_lb(
    daemon_factory, test_agent_with_llm_models
):
    """spawn_instance(agent_id=..., model=None) → load balancing fires."""
    daemon = await daemon_factory()
    result = await daemon.spawn_instance(
        agent_id="lb_test",  # agent has llm_models=[{m1,50},{m2,50}]
        model=None,
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    assert model in ("m1", "m2")  # load-balanced


@pytest.mark.asyncio
async def test_spawn_instance_with_mcp_passes_model(
    daemon_factory, test_agent_with_llm_models
):
    """spawn_instance_with_mcp(model='forced') bypasses load balancing."""
    daemon = await daemon_factory()
    # spawn_instance_with_mcp accepts **kwargs including model
    result = await daemon.spawn_instance_with_mcp(
        agent_id="lb_test",
        model="forced",
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    assert model == "forced"


@pytest.mark.asyncio
async def test_invoke_agent_and_wait_passes_model(
    daemon_factory, test_agent_with_llm_models
):
    """invoke_agent_and_wait(model='forced') bypasses load balancing."""
    daemon = await daemon_factory()
    result = await daemon.invoke_agent_and_wait(
        manager=daemon,
        agent_id="lb_test",
        message="test",
        model="forced",
    )
    # invoke_agent_and_wait returns (result, instance_id)
    instance_id = result[1] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    assert model == "forced"


@pytest.mark.asyncio
async def test_explorer_caller_model_overrides_bypasses_lb(
    daemon_factory, test_agent_with_llm_models
):
    """Explorer caller_model_overrides sets model explicitly → bypasses LB.

    The explorer agent's caller_model_overrides mechanism resolves a model
    and passes it as the spawn-time model parameter. This must bypass
    load balancing for the spawned explorer instance.
    """
    daemon = await daemon_factory()
    # Configure explorer meta.json with caller_model_overrides
    # that maps the calling agent to a specific model
    result = await daemon.spawn_instance_with_mcp(
        agent_id="explorer",
        model="forced-by-caller-override",
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    assert model == "forced-by-caller-override"


@pytest.mark.asyncio
async def test_default_fallback_no_llm_models_no_llm_model(
    daemon_factory, test_agent_fixture
):
    """Agent with no llm_models and no llm_model → uses config default."""
    daemon = await daemon_factory()
    result = await daemon.spawn_instance(
        agent_id="plain_test",  # no llm_models, no llm_model
        model=None,
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    # Should be the config.llm.model default
    assert model == daemon.config.llm.model


@pytest.mark.asyncio
async def test_llm_model_fallback_when_no_llm_models(
    daemon_factory, test_agent_with_llm_model_only
):
    """Agent with llm_model but no llm_models → uses llm_model."""
    daemon = await daemon_factory()
    result = await daemon.spawn_instance(
        agent_id="llm_model_test",  # has llm_model="custom-model"
        model=None,
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    model = await daemon.get_instance_model(instance_id)
    assert model == "custom-model"
```

## Test Coverage Matrix

| Test File | Coverage |
|-----------|----------|
| `test_llm_load_balance.py` | Algorithm unit tests (Phase 2) — all edge cases, statistical correctness, clamping, filtering |
| `test_llm_load_balance_meta_loading.py` | C6 regression (Phase 1) — meta.json loading survives, malformed entries handled gracefully, backward compat |
| `test_llm_load_balance_integration.py` | `_build_llm_config` priority (Phase 3) — all 4 priority levels verified, override always wins |
| `test_llm_load_balance_persistence.py` | DB persistence (Phase 4) — `model_override` populated, JSONB serialization works |
| `test_llm_load_balance_restore.py` | Restore path (Phase 4) — model survives restart, no re-randomization |
| `test_llm_load_balance_http.py` (or extend existing) | End-to-end (Phases 1-4) — HTTP API surfaces model, concurrent spawns distribute correctly |

## CI Integration

- All tests must pass against PostgreSQL (the PRIMARY dev/test DB).
- Statistical tests use `random.seed(42)` for reproducibility.
- Restore test uses fast fixture-based teardown (no actual process kill — mock the shutdown).
- New test files follow existing `tests/` conventions (pytest, asyncio mode, fixtures from `conftest.py`).

## Exit Criterion

- All new tests pass.
- All pre-existing tests still pass (no regression).
- Coverage matrix above is fully populated.
- C6 regression test in place — any future addition of a meta.json field will follow the same pattern.
- Ready to merge.
