"""The configuration resolution (L6) + the reuse rules.

The ``DECISION_*`` names carry no ``OPENAI_`` prefix, so they cannot
ride the prefixed resolution (``LLMConfig.env_prefix="OPENAI_"``) —
they are read EXPLICITLY.

Two resolution paths: the INJECTED house config object (the wiring
round's ``Config.decision`` namespace) and the standalone env read (the
test fallback). The reuse rules are the house's own purpose-bound-model
precedent (``daemon/tools/image_gen.py:263-292``): REUSE, never mint —
and ``OPENAI_MODEL`` is NEVER read.
"""

from __future__ import annotations

from dataclasses import dataclass

from daemon.services.decision.config import (
    DEFAULT_COST_CEILING_USD,
    DEFAULT_DECISION_MODEL,
    DEFAULT_TOKEN_CEILING,
    DecisionConfig,
)


class _Seams:
    """The house ``seams`` sub-model."""

    snapshot_l3_selection_enabled: bool = False
    snapshot_l3_selection_stage: str = "shadow"


@dataclass
class _HouseConfig:
    """A stand-in for the wiring round's ``Config.decision`` namespace.

    The CANONICAL field names are LOWERCASE (the house config is a
    ``BaseSettings`` with ``env_prefix="DECISION_"``); the nested
    ``seams`` sub-model carries the per-seam switches.
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: float = 0.0
    cost_ceiling_usd: float = 0.0
    token_ceiling: int = 0
    pricing_usd_per_million_tokens: str = ""
    global_enabled: bool = False
    seams: object = None


def test_the_injected_house_config_is_read_by_attribute() -> None:
    """L6 — the injected object is the single truth (canonical names)."""
    source = _HouseConfig(
        base_url="https://proxy.test/v1",
        api_key="sk-injected",
        model="decision",
        timeout_s=7.5,
        cost_ceiling_usd=12.5,
        token_ceiling=999,
        pricing_usd_per_million_tokens='{"decision": 2.5}',
        global_enabled=True,
        seams=_Seams(),
    )
    config = DecisionConfig.from_source(source)
    assert config.base_url == "https://proxy.test/v1"
    assert config.api_key == "sk-injected"
    assert config.model == "decision"
    assert config.timeout_s == 7.5
    assert config.cost_ceiling_usd == 12.5
    assert config.token_ceiling == 999
    assert config.rate_for("decision") == 2.5
    assert config.global_enabled is True
    assert config.seams["snapshot_l3_selection"]["enabled"] is False
    assert config.seams["snapshot_l3_selection"]["stage"] == "shadow"


def test_the_uppercase_names_remain_a_tolerant_fallback() -> None:
    """The env-shaped names still resolve (the standalone env path)."""
    config = DecisionConfig.from_source(
        {
            "DECISION_BASE_URL": "https://proxy.test/v1",
            "DECISION_API_KEY": "sk-upper",
            "DECISION_COST_CEILING_USD": "11.5",
        }
    )
    assert config.base_url == "https://proxy.test/v1"
    assert config.api_key == "sk-upper"
    assert config.cost_ceiling_usd == 11.5


def test_the_nested_seams_sub_model_is_the_canonical_shape() -> None:
    """``decision.seams.<flat>_enabled`` / ``_stage`` — the canonical form."""

    class _SeamsModel:
        snapshot_l3_selection_enabled = True
        snapshot_l3_selection_stage = "deciding"

    class _House:
        global_enabled = True
        seams = _SeamsModel()

    config = DecisionConfig.from_source(_House())
    assert config.global_enabled is True
    assert config.seams["snapshot_l3_selection"]["enabled"] is True
    assert config.seams["snapshot_l3_selection"]["stage"] == "deciding"
    tier = config.yaml_switch_tier()
    assert tier["decision_global_enabled"] == "1"
    assert tier["decision_seam_snapshot_l3_selection_enabled"] == "1"
    assert tier["decision_seam_snapshot_l3_selection_stage"] == "deciding"


def test_a_mapping_source_is_accepted_too() -> None:
    """L6 — the same object, two shapes."""
    config = DecisionConfig.from_source(
        {
            "DECISION_BASE_URL": "https://proxy.test/v1/",
            "DECISION_API_KEY": "sk-mapped",
        }
    )
    assert config.base_url == "https://proxy.test/v1"
    assert config.api_key == "sk-mapped"


def test_the_base_url_falls_back_to_the_openai_base_url() -> None:
    """REUSE, never mint — the base URL defaults to ``OPENAI_BASE_URL``."""
    config = DecisionConfig.from_env(
        {"OPENAI_BASE_URL": "https://proxy.test/v1", "OPENAI_API_KEY": "sk-reused"}
    )
    assert config.base_url == "https://proxy.test/v1"
    assert config.api_key == "sk-reused"


def test_the_credential_falls_back_to_the_openai_api_key() -> None:
    """REUSE, never mint — the credential defaults to ``OPENAI_API_KEY``."""
    config = DecisionConfig.from_env({"OPENAI_API_KEY": "sk-reused"})
    assert config.api_key == "sk-reused"


def test_openai_model_is_never_read() -> None:
    """The model is a PURPOSE id — ``OPENAI_MODEL`` is never consulted."""
    config = DecisionConfig.from_env(
        {
            "OPENAI_BASE_URL": "https://proxy.test/v1",
            "OPENAI_API_KEY": "sk-reused",
            "OPENAI_MODEL": "some-chat-model",
        }
    )
    assert config.model == DEFAULT_DECISION_MODEL
    assert config.model == "decision"


def test_an_explicitly_empty_model_disables_the_lane() -> None:
    """D1 — an EXPLICITLY EMPTY ``DECISION_MODEL`` is NOT configured."""
    config = DecisionConfig.from_env(
        {
            "DECISION_BASE_URL": "https://proxy.test/v1",
            "DECISION_API_KEY": "sk-reused",
            "DECISION_MODEL": "   ",
        }
    )
    assert config.model == ""
    assert config.configured is False


def test_the_defaults_are_the_ratified_figures() -> None:
    """The ceiling is USD 50 GLOBAL; the token ceiling is the fail-safe."""
    config = DecisionConfig.from_env({})
    assert config.cost_ceiling_usd == DEFAULT_COST_CEILING_USD
    assert config.cost_ceiling_usd == 50.0
    assert config.token_ceiling == DEFAULT_TOKEN_CEILING
    assert config.timeout_s == 5.0


def test_the_rate_table_accepts_a_json_string() -> None:
    """The operator rate table may arrive as a JSON object string."""
    config = DecisionConfig.from_env(
        {"DECISION_PRICING_USD_PER_MILLION_TOKENS": '{"decision": 3.25}'}
    )
    assert config.rate_for("decision") == 3.25


def test_an_unparseable_rate_table_yields_no_rate() -> None:
    """No rate ⇒ the token ceiling is the fail-safe (never absent)."""
    config = DecisionConfig.from_env(
        {"DECISION_PRICING_USD_PER_MILLION_TOKENS": "not-json"}
    )
    assert config.rate_for("decision") is None


def test_the_decisions_url_never_appends_v1_again() -> None:
    """The base already carries ``/v1``."""
    config = DecisionConfig.from_env(
        {"DECISION_BASE_URL": "https://proxy.test/v1", "DECISION_API_KEY": "k"}
    )
    assert config.decisions_url == "https://proxy.test/v1/decisions"


def test_the_headers_carry_the_bearer_and_the_proxy_app() -> None:
    headers = DecisionConfig.from_env({"DECISION_API_KEY": "k"}).headers()
    assert headers["Authorization"] == "Bearer k"
    assert headers["x-proxy-app"] == "ensemble"
    assert headers["Accept"] == "application/json"
