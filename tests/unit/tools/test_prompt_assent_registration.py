"""Registration + allow-list proof for the ``prompt-assent`` category
(prompt-assent Phase 2, task 2.8/2.10 — base ``c0f599608``).

The three-step registration seam (decorator + registry entries +
construction) is proven END-TO-END here, in the notify-user shape:

* (a) the AST discovery finds ``prompt_assent`` in the category module;
* (b) the decorator order is LOAD-BEARING and pinned:
  ``@register_tool_category`` ABOVE ``@tool``;
* (c) the category is in ``PRIVILEGED_TOOL_CATEGORIES`` (entry #21);
* (d) ``CATEGORY_MODULES["prompt-assent"]`` resolves to a
  ``daemon.tools.*`` path (the AST scanner resolves the value as
  ``daemon/tools/<parts[2:]>.py`` — a ``daemon.services.*`` value is
  never discovered and the drift pin fails at boot);
* (e) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry
  ``prompt_assent`` (factory-built, not import-time registered);
* (f) the ``create_instance_tools`` extend is greppable in source AND
  precedes ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (decorator-only registration is SILENTLY
  INVISIBLE — a late extend bypasses the privileged strip);
* (g) the ari-ONLY census: ``prompt-assent`` appears in
  ``agents/ari/meta.json`` and NOWHERE ELSE across every
  ``agents/*/meta.json``;
* (h) the in-body runtime gate exists in source (layer 3 — a literal
  tool name in an allow list bypasses the category gate);
* (i) the ACTION SET is EXACTLY the four AR-2 verbs (a 3-verb or a
  5-verb set is a pin failure);
* (j) the REFUSAL TAXONOMY matches architect §3 EXACTLY (no missing
  code, no extra code) and the eight codes THIS phase places are a
  subset of it;
* (k) the gate has exactly ONE implementation site (O-E — grep-proven).

MUTATION VERIFICATION: the ari-only census, the decorator order and the
action-set pins are the classic prose-satisfiable traps — each one is
covered by a counter-mutation in
``tests/unit/test_prompt_assent_lane.py``'s mutation notes and by the
pack leg ``MT-28`` in ``test/packs/metrics_read_mutation_matrix_test.py``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from daemon.config import PromptAssentConfig
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.prompt_assent import (
    PHASE2_PLACED_CODES,
    PROMPT_ASSENT_ACTIONS,
    PROMPT_ASSENT_AUTHORIZED_AGENT,
    PROMPT_ASSENT_CATEGORY_KEY,
    PROMPT_ASSENT_DEFAULT_ENABLED,
    PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
    PROMPT_ASSENT_TOOL_NAME,
    REFUSAL_TAXONOMY,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

CATEGORY = "prompt-assent"
MODULE = "daemon.tools.prompt_assent"
TOOL_NAME = "prompt_assent"

#: The COMPLETE architect §3 refusal taxonomy (verbatim) — the pin
#: asserts the module's constant matches it EXACTLY.
ARCHITECT_REFUSAL_TAXONOMY: frozenset[str] = frozenset({
    "lane-disabled",
    "not-ari",
    "invalid-arguments",
    "user-confirmation-missing",
    "no-user-origin-turn",
    "user-origin-window-expired",
    "user-origin-window-stale",
    "assent-verification-unavailable",
    "token-mismatch",
    "token-expired",
    "token-already-used",
    "token-superseded",
    "token-item-mismatch",
    "token-content-mismatch",
    "token-channel-mismatch",
    "token-approver-mismatch",
    "mint-requires-private-channel",
    "mint-live-token-exists",
    "token-locked",
    "mismatch-budget-exceeded",
    "item-not-found",
    "item-not-assentable",
    "item-not-pending",
    "already-decided",
    "self-approval-attempt",
    "extraction-incomplete",
    "audit-unavailable",
})

#: The EIGHT codes this phase places (the amendment's list).
EXPECTED_PLACED_CODES: frozenset[str] = frozenset({
    "invalid-arguments",
    "token-content-mismatch",
    "token-channel-mismatch",
    "mint-requires-private-channel",
    "mint-live-token-exists",
    "extraction-incomplete",
    "token-approver-mismatch",
    "mismatch-budget-exceeded",
})


# ── the static registration points ───────────────────────────────────────


class TestStaticRegistration:
    def test_step1_ast_discovery_finds_prompt_assent(self) -> None:
        """Step 1 — the AST walker finds the factory-created ``@tool``
        function at its nesting depth inside the factory."""
        discovered = discover_source_only_tool_names()
        assert TOOL_NAME in discovered, (
            f"AST discovery missed {TOOL_NAME!r}"
        )

    def test_step1b_the_module_ast_carries_the_decorator_order(self) -> None:
        """The decorator order is LOAD-BEARING: if ``@tool`` ran first,
        the wrapper would carry the category but the strip reads the
        WRAPPED object — the order must be ``@register_tool_category``
        ABOVE ``@tool``."""
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        orders: list[tuple[str, ...]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [
                    d.func.id
                    for d in node.decorator_list
                    if isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Name)
                ]
                if "register_tool_category" in decorators:
                    orders.append(tuple(decorators))
        assert orders, "no @register_tool_category-decorated tool found"
        assert ("register_tool_category", "tool") in orders, (
            f"decorator order drifted: {orders} — register_tool_category "
            "must sit ABOVE @tool"
        )

    def test_step2_category_modules_entry(self) -> None:
        """Step 2 — ``CATEGORY_MODULES['prompt-assent']`` points at the
        module, and it is a ``daemon.tools.*`` path."""
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        assert MODULE.startswith("daemon.tools."), (
            f"{MODULE} is not a daemon.tools.* path — a daemon.services.* "
            "value is NEVER discovered and the drift pin fails at boot"
        )
        parts = MODULE.split(".")
        assert (REPO_ROOT / "daemon" / "tools" / f"{parts[-1]}.py").is_file()

    def test_step3_the_tool_names_are_registered(self) -> None:
        """Step 3 — the factory-built tool carries both name sets."""
        assert TOOL_NAME in DYNAMIC_TOOL_NAMES
        assert TOOL_NAME in KNOWN_TOOL_NAMES

    def test_step4_the_category_is_privileged(self) -> None:
        """Step 4 — the category is PRIVILEGED (entry #21): the
        empty-allow inherit universe must NEVER auto-grant the lane."""
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 21


class TestTheAriOnlyCensus:
    def test_the_ari_meta_carries_the_category(self) -> None:
        meta = json.loads(
            (REPO_ROOT / "agents" / "ari" / "meta.json").read_text(encoding="utf-8")
        )
        allow = meta.get("tools", {}).get("allow", []) or []
        assert CATEGORY in allow
        assert allow[-1] == CATEGORY, (
            "the prompt-assent entry (2026-11-10) must be the LAST entry of "
            "ari's allow array (the trailing-entry discipline)"
        )

    def test_no_other_agent_carries_the_category(self) -> None:
        offenders: list[str] = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            if meta_path.parent.name == "ari":
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", []) or []
            if CATEGORY in allow:
                offenders.append(str(meta_path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            f"the prompt-assent category is ARI-ONLY; found in: {offenders}"
        )


class TestTheInstanceToolsExtend:
    def test_the_extend_exists_and_precedes_the_help_and_the_strip(self) -> None:
        """The extend MUST precede ``create_help_tool`` (which snapshots
        the tool list into its index), ``scan_tools_for_full_docs`` and
        ``_apply_tool_filter`` (the privileged-category default-deny
        strip). A later extend is either invisible to the help tool or
        BYPASSES the privileged strip."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        extend_at = source.find("create_prompt_assent_tools_if_available(")
        assert extend_at != -1, "the prompt-assent extend is missing"
        for marker in (
            "create_help_tool(",
            "scan_tools_for_full_docs(",
            "_apply_tool_filter(",
        ):
            marker_at = source.find(marker)
            assert marker_at != -1, f"the marker {marker!r} vanished"
            assert extend_at < marker_at, (
                f"the prompt-assent extend must precede {marker!r} "
                "(placement is load-bearing)"
            )

    def test_the_factory_always_constructs(self) -> None:
        """The factory ALWAYS constructs — the kill-switch is a per-call
        refusal, never a construction-absence (D-2)."""
        from daemon.tools.prompt_assent import create_prompt_assent_tools

        tools = create_prompt_assent_tools(None, current_instance_id="", agent_id="")
        assert [t.name for t in tools] == [TOOL_NAME]
        assert getattr(tools[0], "_tool_category", None) == CATEGORY


class TestTheInBodyGate:
    def test_the_authorized_agent_constant_is_ari(self) -> None:
        assert PROMPT_ASSENT_AUTHORIZED_AGENT == "ari"

    def test_the_body_gate_precedes_the_kill_switch(self) -> None:
        """The auth gate is FIRST: an unauthorized caller touches nothing
        else — no switch read, no window read, no ledger noise."""
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        gate_at = source.find("caller_agent_id != PROMPT_ASSENT_AUTHORIZED_AGENT")
        switch_at = source.find("if not _resolve_enabled(")
        assert gate_at != -1 and switch_at != -1
        assert gate_at < switch_at, (
            "the ari layer-3 gate must precede the kill-switch read"
        )

    def test_the_gate_has_exactly_one_implementation_site(self) -> None:
        """O-E — the three-factor gate has exactly ONE implementation
        owner: this file. A second site is a pin failure."""
        hits: list[str] = []
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "_run_factor_gates" in text and path.name != "prompt_assent.py":
                hits.append(str(path.relative_to(REPO_ROOT)))
        assert hits == [], (
            f"the factor gate must be implemented in ONE file; found: {hits}"
        )


class TestTheActionSet:
    def test_the_action_set_is_exactly_the_four_verbs(self) -> None:
        """AR-2 — the action set is EXACTLY {list, mint, approve, reject}.
        A 3-verb or a 5-verb set is a pin failure."""
        assert set(PROMPT_ASSENT_ACTIONS) == {
            "list",
            "mint",
            "approve",
            "reject",
        }
        assert len(PROMPT_ASSENT_ACTIONS) == 4

    def test_the_schema_names_the_same_four_verbs(self) -> None:
        """The INPUT SCHEMA's field description names the same four verbs
        — the schema is the surface the LLM sees. The schema field is a
        plain ``str`` on purpose: a ``Literal`` would make LangChain
        REJECT an unknown verb with a validation error instead of the
        lane's own typed ``invalid-arguments`` refusal (tools never
        raise)."""
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        assert "list | mint | approve | reject" in source
        for verb in PROMPT_ASSENT_ACTIONS:
            assert f'"{verb}"' in source, verb


class TestTheRefusalTaxonomy:
    def test_the_taxonomy_matches_the_architect_exactly(self) -> None:
        """Architect §3 — the COMPLETE code set, no missing code and no
        extra code."""
        assert REFUSAL_TAXONOMY == ARCHITECT_REFUSAL_TAXONOMY, (
            "the refusal taxonomy diverged from architect §3: "
            f"missing={sorted(ARCHITECT_REFUSAL_TAXONOMY - REFUSAL_TAXONOMY)} "
            f"extra={sorted(REFUSAL_TAXONOMY - ARCHITECT_REFUSAL_TAXONOMY)}"
        )

    def test_the_eight_placed_codes_are_a_subset(self) -> None:
        assert PHASE2_PLACED_CODES == EXPECTED_PLACED_CODES
        assert PHASE2_PLACED_CODES <= REFUSAL_TAXONOMY

    def test_every_placed_code_is_referenced_in_the_module(self) -> None:
        """A placed code must actually be EMITTED by the module — a code
        that only lives in a constant is prose-satisfiable."""
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        for code in sorted(PHASE2_PLACED_CODES):
            assert f'"{code}"' in source, f"{code} is never referenced"


class TestTheSharedDefaults:
    def test_the_kill_switch_default_is_off(self) -> None:
        """D-2 — the ONE shared default constant is OFF (fail-closed).
        Phase 4's ``PromptAssentConfig`` class consumes the SAME
        constant, so the two can never disagree."""
        assert PROMPT_ASSENT_DEFAULT_ENABLED is False

    def test_the_budget_default_is_ten(self) -> None:
        """K-9 / D-3b — the ONE default for the hourly mismatch budget."""
        assert PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET == 10

    def test_the_module_does_not_re_declare_a_second_default(self) -> None:
        """The defaults live in ONE place — ``daemon/config.py``, beside
        the ``PromptAssentConfig`` class (the M5 pattern). BOTH
        consumers — that class (its field defaults) and the tool's
        call-time resolver (this module's import) — read the SAME
        constants; a second literal here would let them drift."""
        config_source = (REPO_ROOT / "daemon" / "config.py").read_text(
            encoding="utf-8"
        )
        assert config_source.count(
            "PROMPT_ASSENT_DEFAULT_ENABLED: bool = False"
        ) == 1
        assert config_source.count(
            "PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET: int = 10"
        ) == 1
        tool_source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        assert (
            "PROMPT_ASSENT_DEFAULT_ENABLED: bool" not in tool_source
        ), "the tool module must IMPORT the shared default, never re-declare it"
        assert (
            "PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET: int"
            not in tool_source
        ), "the tool module must IMPORT the shared default, never re-declare it"
        # The class's field defaults ARE the shared constants (D-2:
        # ONE constant, TWO consumers — this class + the resolver).
        assert (
            PromptAssentConfig.model_fields["enabled"].default
            == PROMPT_ASSENT_DEFAULT_ENABLED
            is False
        )
        assert (
            PromptAssentConfig.model_fields[
                "hourly_mismatch_budget"
            ].default
            == PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
            == 10
        )


class TestTheCategoryKey:
    def test_the_key_is_byte_identical_everywhere(self) -> None:
        assert PROMPT_ASSENT_CATEGORY_KEY == "prompt-assent"
        assert CATEGORY == PROMPT_ASSENT_CATEGORY_KEY
        assert PROMPT_ASSENT_TOOL_NAME == "prompt_assent"
