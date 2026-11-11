"""The prompt-assent KILL-SWITCH — Phase 4 (tasks 4.1–4.3).

Phase 2 pinned the resolver's BEHAVIOUR against the env tier only (its
``FakeManager`` carries no config). Phase 4 lands the
:class:`~daemon.config.PromptAssentConfig` class and wires the REAL
call-time resolver, so this file pins the Phase-4 surface:

* the class exists under the CANONICAL name (architect §7 defect 1 —
  ``PromptAssentConfig`` / ``ENSEMBLE_PROMPT_ASSENT_``; the retired
  ``PromptAssist`` spellings are grepped by
  ``tests/unit/test_prompt_assent_pins.py``);
* the class is WIRED into ``Config`` (the ``:6826``-style site);
* the config tier drives the switch — the boot-resolved value arms or
  disarms the lane with NO env var set;
* the resolver reads AT CALL TIME — mutating the live config object (or
  the env) between calls flips the lane without a restart;
* the per-call ENV tier beats the boot-config tier (the notify_user
  ``_resolve_bounds`` precedence);
* an INDETERMINATE env value fails CLOSED — even against an ARMED boot
  config (D-2 verbatim: the shared OFF default, never the boot value);
* ``mint`` is refused when the lane is off (config tier this time);
* the ``lane-disabled`` refusal NAMES ``ENSEMBLE_PROMPT_ASSENT_ENABLED``;
* the not-ari GENERIC envelope still wins over the switch (gate order —
  an unauthorised caller must not be able to probe the knob's state);
* the DEFAULT state is ``lane-disabled`` out of the box — including
  with a full real ``Config`` object present;
* the ON-path test ARMS THE ENV EXPLICITLY (the plan's task-4.4
  requirement — with the default OFF, the lane's default state is
  ``lane-disabled`` out of the box, so an unarmed E2E would be vacuous);
* the K-9 budget KEY rides the class: a config-configured budget feeds
  the P2-owned in-RAM mechanism (D-3b — the key lands in P4, the
  mechanism stays P2's).

MUTATION VERIFICATION (house rule — the commission names two):
the counter-mutations that REDDEN these pins are demonstrated LIVE in
the lane report:

* **the shared-constant flip** (``daemon/config.py``
  ``PROMPT_ASSENT_DEFAULT_ENABLED`` ``False`` → ``True``) reddens BOTH
  ``test_the_default_state_is_lane_disabled_out_of_the_box`` (the
  fail-closed pin) AND the registration file's shared-constant pin;
* **the fail-open coercion** (``_coerce_switch`` returning ``True`` on
  an indeterminate value) reddens
  ``test_an_invalid_env_fails_closed_even_against_an_armed_config``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_settings import BaseSettings

from daemon.config import Config, PromptAssentConfig
from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
    PROMPT_ASSENT_ENV_PREFIX,
    _MISMATCH_BUDGET,
    _budget_count,
    _hour_bucket,
    _resolve_enabled,
    _resolve_hourly_budget,
    reset_mismatch_budget,
)
from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    RELAY_INSTANCE_ID,
    build_tools,
)

#: The budget env knob name — falls out of the model's own prefix.
BUDGET_ENV = f"{PROMPT_ASSENT_ENV_PREFIX}HOURLY_MISMATCH_BUDGET"


def _manager_with_config(config: Any) -> Any:
    """A wired flow rig whose manager carries a REAL config section."""
    flow = build_flow()
    flow.manager.config = SimpleNamespace(prompt_assent=config)
    return flow


# ── 4.1 — the config class ───────────────────────────────────────────────


class TestThePhase4ConfigClass:
    def test_the_class_exists_under_the_canonical_name(self) -> None:
        """Architect §7 defect 1 / H-11 — the CANONICAL spellings only."""
        from daemon import config as config_module

        assert hasattr(config_module, "PromptAssentConfig")
        assert issubclass(PromptAssentConfig, BaseSettings)
        assert config_module.PromptAssentConfig is PromptAssentConfig

    def test_the_env_prefix_is_byte_exact(self) -> None:
        assert PromptAssentConfig.model_config.get("env_prefix") == (
            "ENSEMBLE_PROMPT_ASSENT_"
        )
        assert KILL_SWITCH_ENV == "ENSEMBLE_PROMPT_ASSENT_ENABLED"
        assert BUDGET_ENV == "ENSEMBLE_PROMPT_ASSENT_HOURLY_MISMATCH_BUDGET"

    def test_the_field_defaults_are_the_shared_constants(self) -> None:
        """D-2 — ONE constant, TWO consumers: the class's field default
        IS the shared fail-closed OFF constant (the resolver imports the
        same name; the single-home pin proves no second literal)."""
        from daemon.config import (
            PROMPT_ASSENT_DEFAULT_ENABLED,
            PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
        )

        assert PROMPT_ASSENT_DEFAULT_ENABLED is False
        assert PromptAssentConfig.model_fields["enabled"].default is False
        assert PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET == 10
        assert (
            PromptAssentConfig.model_fields["hourly_mismatch_budget"].default
            == PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
        )

    def test_the_class_is_wired_into_config(self) -> None:
        """The ``:6826``-style wiring — ``Config.prompt_assent`` is the
        class, default-constructed."""
        assert "prompt_assent" in Config.model_fields
        assert (
            Config.model_fields["prompt_assent"].default_factory
            is PromptAssentConfig
        )
        config = Config()
        assert isinstance(config.prompt_assent, PromptAssentConfig)
        assert config.prompt_assent.enabled is False
        assert (
            config.prompt_assent.hourly_mismatch_budget
            == PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
        )

    def test_the_prefix_tracker_covers_the_knobs(self) -> None:
        """The tests' ``ENSEMBLE_`` prefix tracker (conftest) auto-covers
        BOTH knob names — no conftest edit was made for this class."""
        from tests.conftest import _TRACKED_ENV_PREFIXES, _is_tracked_env

        assert any(
            p == "ENSEMBLE_" for p in _TRACKED_ENV_PREFIXES
        ), "the tracker lost the ENSEMBLE_ prefix"
        assert _is_tracked_env(KILL_SWITCH_ENV)
        assert _is_tracked_env(BUDGET_ENV)


# ── 4.2/4.3 — the call-time resolver ─────────────────────────────────────


class TestTheConfigTierDrivesTheSwitch:
    async def test_an_armed_config_opens_the_lane_without_env(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=True))
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint

    async def test_a_disarmed_config_refuses_the_lane_without_env(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        result = await flow.tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in result, result

    async def test_a_real_default_config_object_is_off_out_of_the_box(
        self, monkeypatch
    ) -> None:
        """The Phase-4 delta on the P2 default-OFF cell: even with a
        FULL real ``Config`` object present (the production shape), the
        default state is ``lane-disabled``."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(Config().prompt_assent)
        for action in ("list", "mint", "approve", "reject"):
            result = await flow.tools["prompt_assent"].ainvoke(
                {"action": action}
            )
            assert "reason=lane-disabled" in result, (action, result)

    async def test_the_config_object_is_read_per_call(
        self, monkeypatch
    ) -> None:
        """Call-time read — flipping the LIVE config object between
        calls flips the lane with no restart and no tool rebuild."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        tools = flow.tools
        blocked = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in blocked
        flow.manager.config.prompt_assent = PromptAssentConfig(enabled=True)
        opened = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" not in opened, opened


class TestTheEnvTierBeatsTheConfigTier:
    async def test_the_env_arms_against_a_disarmed_config(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint

    async def test_the_env_disarms_against_an_armed_config(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        flow = _manager_with_config(PromptAssentConfig(enabled=True))
        result = await flow.tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in result, result

    async def test_an_invalid_env_fails_closed_even_against_an_armed_config(
        self, monkeypatch
    ) -> None:
        """D-2 verbatim: an INDETERMINATE value fails CLOSED — to the
        shared OFF default, NEVER to the boot value and never open."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "maybe")
        flow = _manager_with_config(PromptAssentConfig(enabled=True))
        result = await flow.tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in result, result

    async def test_the_invalid_env_warning_names_the_knob(
        self, monkeypatch, caplog
    ) -> None:
        import logging

        monkeypatch.setenv(KILL_SWITCH_ENV, "perhaps")
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        await flow.tools["prompt_assent"].ainvoke({"action": "list"})
        warnings = [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING and "perhaps" in r.getMessage()
        ]
        assert warnings, "the invalid knob must be named in a WARNING"
        assert KILL_SWITCH_ENV in warnings[0]


class TestTheDisabledLaneShape:
    async def test_mint_is_refused_and_writes_no_token_row(
        self, monkeypatch
    ) -> None:
        """``mint`` is refused too (D-2) — via the CONFIG tier this
        time — and the refusal writes NO token-table row (the token
        table records mints, not refusals — O-G)."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        result = await flow.tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": flow.version_id}
        )
        assert "reason=lane-disabled" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_the_refusal_names_the_env_var(self, monkeypatch) -> None:
        """A disabled lane refuses cleanly with a typed code NAMING the
        env var — never a silent fall-back-to-FE confusion (D-2)."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        result = await flow.tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in result
        assert KILL_SWITCH_ENV in result, (
            "the refusal must name ENSEMBLE_PROMPT_ASSENT_ENABLED"
        )

    async def test_the_tool_stays_registered_when_the_config_disarms(
        self, monkeypatch
    ) -> None:
        """Refusal-at-call, never construction-absence — the config
        tier must not shrink the surface either."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=False))
        assert "prompt_assent" in build_tools(flow.manager)

    async def test_the_not_ari_envelope_wins_over_an_armed_switch(
        self, monkeypatch
    ) -> None:
        """Gate order (D-2 condition ii): the ari layer-3 gate precedes
        the switch read — an unauthorised caller cannot probe the
        knob's state even when the lane is ARMED, and the envelope
        carries NO refusal code."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        flow = _manager_with_config(PromptAssentConfig(enabled=True))
        other = build_tools(flow.manager, agent_id="coder")
        result = await other["prompt_assent"].ainvoke({"action": "list"})
        assert result.startswith(
            "ERROR: prompt_assent is available to the ari agent only"
        ), result
        assert "reason=" not in result
        assert "lane-disabled" not in result


class TestTheOnPathArmsTheEnvExplicitly:
    async def test_the_on_path_arms_the_env_and_completes_a_mint(
        self, monkeypatch
    ) -> None:
        """The task-4.4 ON-path cell: the env is armed EXPLICITLY (with
        the default OFF, the lane's default state is ``lane-disabled``
        out of the box — an unarmed E2E would be vacuous)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        assert "token: CONFIRM-" in mint, mint
        assert "render_sha256: " in mint, mint


# ── 4.1 — the budget KEY rides the class (the mechanism stays P2's) ──────


class TestTheBudgetKeyRidesTheClass:
    async def test_a_config_configured_budget_feeds_the_p2_mechanism(
        self, monkeypatch
    ) -> None:
        """D-3b: the P4 class lands the KEY; the P2-owned in-RAM
        mechanism consumes it. A budget of 3 configured on the class
        makes the FOURTH mismatch ``mismatch-budget-exceeded``."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.delenv(BUDGET_ENV, raising=False)
        flow = _manager_with_config(
            PromptAssentConfig(hourly_mismatch_budget=3)
        )
        await mint_for(flow)
        unknown = "CONFIRM-ZZZZZZZZ"
        echo(flow, unknown)
        for i in range(3):
            result = await decide(flow, token=unknown)
            assert "reason=token-mismatch" in result, (i, result)
        overflow = await decide(flow, token=unknown)
        assert "reason=mismatch-budget-exceeded" in overflow, overflow
        assert _budget_count(RELAY_INSTANCE_ID, _hour_bucket()) == 3

    async def test_the_budget_env_tier_beats_the_config_tier(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(BUDGET_ENV, "2")
        flow = _manager_with_config(
            PromptAssentConfig(hourly_mismatch_budget=50)
        )
        assert _resolve_hourly_budget(flow.manager) == 2

    async def test_an_invalid_budget_env_repairs_to_the_config_value(
        self, monkeypatch
    ) -> None:
        """The notify_user doctrine: one invalid knob must not blind the
        others — the budget repairs per-knob to its validated fallback
        (the boot config value) and is named in a warning."""
        import logging

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(BUDGET_ENV, "not-a-number")
        flow = _manager_with_config(
            PromptAssentConfig(hourly_mismatch_budget=7)
        )
        assert _resolve_hourly_budget(flow.manager) == 7
        assert _resolve_enabled(flow.manager) is True

    async def test_the_budget_default_is_the_shared_constant(self) -> None:
        assert (
            _resolve_hourly_budget(None)
            == PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
            == 10
        )


# ── order-independence hygiene ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_budget():
    """The K-9 counter is in-RAM — every test starts from a clean slate
    (test-order independence across the matrix files)."""
    reset_mismatch_budget()
    yield
    reset_mismatch_budget()
    assert not _MISMATCH_BUDGET
