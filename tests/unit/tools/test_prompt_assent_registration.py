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
* (j) the REFUSAL TAXONOMY matches architect §3 as amended by the
  prompt-assent Phase-1.5 lane (+2 codes) EXACTLY (no missing code, no
  extra code); the eight codes the parent phase places are a subset of
  it, and the TWO Phase-1.5 codes are pinned by their own equality;
* (k) the gate has exactly ONE implementation site (O-E — grep-proven);
* (l) the Phase-1.5 structural pins: the designation resolver symbol
  appears in NO DM-reachable function, the tool's input schema still
  carries NO identity field, ``MISMATCH_CODES`` is pinned by EXACT
  EQUALITY with the two new codes proven ABSENT, and the designation
  env-name literal is not duplicated in ``daemon/``.

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
    MISMATCH_CODES,
    PHASE1_5_PLACED_CODES,
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

#: The architect §3 refusal taxonomy **as amended by the prompt-assent
#: Phase-1.5 lane** (+2 codes: ``approver-not-pinned`` /
#: ``designation-config-invalid`` — decisions.md **D-8** / **D-13**). The
#: parent set stays frozen at 27 codes and is quoted verbatim here; the
#: amendment is the two trailing entries. The pin asserts the module's
#: constant matches this set EXACTLY (no missing code, no extra code).
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
    # ── Phase-1.5 amendment (D-13): +2 codes ──────────────────────────
    "approver-not-pinned",
    "designation-config-invalid",
})

#: The EIGHT codes the PARENT phase places (the amendment's list). It
#: stays at EIGHT — the Phase-1.5 codes ship as their own set below.
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

#: The TWO codes the Phase-1.5 lane places — pinned by EQUALITY (not
#: membership), and proven DISJOINT from ``MISMATCH_CODES`` (no burn).
EXPECTED_PHASE1_5_PLACED_CODES: frozenset[str] = frozenset({
    "approver-not-pinned",
    "designation-config-invalid",
})

#: The MISMATCH-class set, pinned by EXACT EQUALITY. The two Phase-1.5
#: codes must NEVER appear here: a mismatch-class code consumes the K-9
#: hourly budget and increments the durable per-token counter, which would
#: turn an operator typo or a re-designation into a token-revocation DoS
#: (refusal-cell-matrix §1.3 / D-2.4).
EXPECTED_MISMATCH_CODES: frozenset[str] = frozenset({
    "token-mismatch",
    "token-item-mismatch",
    "token-content-mismatch",
    "token-channel-mismatch",
    "token-approver-mismatch",
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


class TestTheAriExactlyPin:
    """The ari-EXACTLY census, re-asserted after the Phase-1.5 edit
    (T2.6 / blocker #8).

    ``prompt_assent.py`` is edited heavily by this lane; the THREE-part
    audience rule must not drift: the privileged category (never
    default-granted), ari's explicit allow entry (and NOBODY else's), and
    the in-body layer-3 gate. A literal tool name in an allow list bypasses
    the category gate, so the category alone cannot spell the audience.
    """

    def test_the_privileged_category_still_holds_the_lane(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 21

    def test_ari_is_the_only_allow_holder(self) -> None:
        holders: list[str] = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", []) or []
            if CATEGORY in allow:
                holders.append(meta_path.parent.name)
        assert holders == ["ari"], (
            f"prompt-assent must remain ari-EXACTLY; holders={holders}"
        )

    def test_the_in_body_layer_three_gate_still_exists(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        assert "caller_agent_id != PROMPT_ASSENT_AUTHORIZED_AGENT" in source
        assert PROMPT_ASSENT_AUTHORIZED_AGENT == "ari"


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


class TestThePhase1_5PlacedCodes:
    """The Phase-1.5 placement pins (T2.6 / T2.7 / D-18 / D-2.4)."""

    def test_the_two_new_codes_are_in_the_taxonomy(self) -> None:
        assert PHASE1_5_PLACED_CODES == EXPECTED_PHASE1_5_PLACED_CODES
        assert PHASE1_5_PLACED_CODES <= REFUSAL_TAXONOMY
        assert len(REFUSAL_TAXONOMY) == 29, (
            "the taxonomy is 27 + exactly 2 (D-8); a third code is a "
            "taxonomy growth the lane did not rule"
        )

    def test_every_phase1_5_code_is_referenced_in_the_module(self) -> None:
        """A placed code must actually be EMITTED by the module — a code
        that only lives in a constant is prose-satisfiable."""
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_assent.py").read_text(
            encoding="utf-8"
        )
        for code in sorted(PHASE1_5_PLACED_CODES):
            assert f'"{code}"' in source, f"{code} is never referenced"

    def test_the_parent_placed_set_did_not_move(self) -> None:
        """``PHASE2_PLACED_CODES`` stays at its EIGHT — the Phase-1.5 codes
        ship as their own set (architect §6.4)."""
        assert len(PHASE2_PLACED_CODES) == 8
        assert PHASE1_5_PLACED_CODES.isdisjoint(PHASE2_PLACED_CODES)

    def test_mismatch_codes_exact_equality(self) -> None:
        """P-3 (T2.7 (iii)) — ``MISMATCH_CODES`` pinned by EXACT EQUALITY
        (it was membership-only before this lane), and the two new codes are
        proven ABSENT: the burn rule is a security property, not a comment.
        """
        assert MISMATCH_CODES == EXPECTED_MISMATCH_CODES, (
            "MISMATCH_CODES drifted — a mismatch-class code burns the K-9 "
            "budget and the durable counter"
        )
        assert PHASE1_5_PLACED_CODES.isdisjoint(MISMATCH_CODES), (
            "a Phase-1.5 code in MISMATCH_CODES would let a stranger or an "
            "operator typo revoke the Emperor's live token"
        )
        assert "token-approver-mismatch" in MISMATCH_CODES, (
            "token-approver-mismatch keeps ONLY its 4e different-human role — "
            "it is never the approve-side mapping of approver-not-pinned"
        )

    def test_the_approve_side_mapping_never_uses_token_approver_mismatch(
        self,
    ) -> None:
        """NO-BURN MAPPING (D-18 / AC18): the approve-side designation table
        maps APPROVER_NOT_PINNED to ``approver-not-pinned``, never to the
        burning ``token-approver-mismatch``."""
        from daemon.repositories.prompt_assent.mint_context import (
            DesignationResult,
        )
        from daemon.services.prompt_version_service import (
            _approve_designation_codes,
        )

        table = _approve_designation_codes()
        assert set(table) == set(DesignationResult) - {DesignationResult.PASS}
        assert table[DesignationResult.APPROVER_NOT_PINNED] == "approver-not-pinned"
        assert "token-approver-mismatch" not in table.values()
        # every mapped value is a real taxonomy code
        assert set(table.values()) <= REFUSAL_TAXONOMY

    def test_the_mint_side_mapping_never_uses_a_mismatch_code(self) -> None:
        from daemon.repositories.prompt_assent.mint_context import (
            DesignationResult,
        )
        from daemon.tools.prompt_assent import _MINT_DESIGNATION_CODES

        table = _MINT_DESIGNATION_CODES
        assert set(table) == set(DesignationResult) - {DesignationResult.PASS}
        assert table[DesignationResult.APPROVER_NOT_PINNED] == "approver-not-pinned"
        assert table[DesignationResult.CONFIG_INVALID] == "designation-config-invalid"
        assert table[DesignationResult.NOT_DESIGNATED] == "mint-requires-private-channel"
        assert table[DesignationResult.GROUP_DM_SHAPE] == "mint-requires-private-channel"
        assert table[DesignationResult.ABSENT_DISCRIMINATOR] == (
            "mint-requires-private-channel"
        )
        assert table[DesignationResult.ABSENT_IDENTITY] == "extraction-incomplete"
        # The mint path never emits a MISMATCH_CLASS code at all.
        assert not (set(table.values()) & MISMATCH_CODES)


class TestTheDesignationStructuralPins:
    """P-2 / P-4 / P-1-from-the-test-side (T2.7)."""

    def test_the_resolver_symbol_is_in_no_dm_reachable_function(self) -> None:
        """P-2 (the source-shape leg of INVARIANT-DM): the designation
        resolver is called ONLY from the non-DM arms — the DM short-circuits
        (``_AssentContext.is_private`` in the mint gate, ``context.is_private``
        at 4f) precede every resolver call, and the DM path never reaches a
        branch that asks for the map.

        Shape check: the ONLY call sites of ``_resolve_designations`` and of
        ``designations.get()`` / ``designations.outcome`` / ``designations.digest``
        are inside ``_mint_branch`` (AFTER the ``is_private`` short-circuit)
        and ``_decide_branch`` (INSIDE the ``not context.is_private`` arm).
        """
        import ast

        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        branch_source: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                "_mint_branch",
                "_decide_branch",
                "_dispatch",
                "_run_factor_gates",
                "_accept_via_service",
                "_build_attestation",
            }:
                branch_source[node.name] = ast.get_source_segment(source, node) or ""

        # ``_dispatch`` constructs the cache but never resolves it; the
        # factor gate / attestation paths never touch it at all.
        for name in ("_run_factor_gates", "_accept_via_service", "_build_attestation"):
            assert "designations" not in branch_source[name], (
                f"{name} must not touch the designation map"
            )
        assert ".get()" not in branch_source["_dispatch"]
        assert ".outcome(" not in branch_source["_dispatch"]
        assert ".digest" not in branch_source["_dispatch"]

        # In the mint branch, the resolver is reached only AFTER the DM
        # short-circuit.
        mint_src = branch_source["_mint_branch"]
        short_circuit_at = mint_src.find("if not context.is_private:")
        assert short_circuit_at != -1
        assert mint_src.find("designations.outcome(") > short_circuit_at
        assert mint_src.find("designations.digest") > short_circuit_at

        # In the decide branch, the resolver is reached only inside the
        # ``not context.is_private`` arm (the DM leg sets the code to None).
        decide_src = branch_source["_decide_branch"]
        assert "if context.is_private:" in decide_src
        assert decide_src.find("assent_channel_not_private_code(") > (
            decide_src.find("if context.is_private:")
        )

    def test_the_resolution_site_is_one_function(self) -> None:
        """O-E analogue: the environment read lives in ONE function."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert source.count("os.environ.get(ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS)") == 1
        assert source.count("def _resolve_designations(") == 1

    def test_the_input_schema_carries_no_identity_field(self) -> None:
        """P-4 (the parent's R3 analogue, re-asserted here): a channel id, a
        user id or a map reference in the tool's args would be a
        client-supplied trust surface — the exact forgery the lane refuses."""
        from tests.helpers.prompt_assent_tool_fixture import (
            FakeManager,
            build_tools,
        )

        tools = build_tools(FakeManager())
        schema = set(tools["prompt_assent"].args_schema.model_fields)
        assert schema == {
            "action", "version_id", "token", "user_confirmed",
            "reject_reason", "limit", "force",
        }
        assert not (schema & {"channel_id", "user_id", "designations", "map_digest"})

    def test_the_env_literal_is_not_duplicated_in_daemon(self) -> None:
        """P-1 asserted from the test side: exactly ONE literal in
        ``daemon/`` — the declaration site in ``designation.py``."""
        from daemon.repositories.prompt_assent.designation import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
        )

        literal = f'"{ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS}"'
        hits: list[tuple[str, int]] = []
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            count = path.read_text(encoding="utf-8", errors="ignore").count(literal)
            if count:
                hits.append((str(path.relative_to(REPO_ROOT)), count))
        assert hits == [("daemon/repositories/prompt_assent/designation.py", 1)], hits

    def test_the_parse_designations_call_is_the_only_parser_entry(self) -> None:
        """The parser has ONE entry point — no second parse implementation."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert source.count("parse_designations(") == 1


class TestThePhase1_5CodeShape:
    """T2.5 / T2.17 / T2.16 / T2.10 / T2.11 / T2.15 structural pins."""

    def test_the_4f_split_refuses_before_record_mismatch(self) -> None:
        """THE 4f SPLIT (T2.5 — BLOCKING): a Phase-1.5 code must return
        BEFORE ``_record_mismatch`` — five such refusals would otherwise
        revoke the Emperor's live token."""
        import ast
        import textwrap

        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        decide = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_decide_branch"
        )
        seg = textwrap.dedent(ast.get_source_segment(source, decide) or "")
        block_at = seg.find("private_code is not None")
        assert block_at != -1
        block = seg[block_at : block_at + 1400]
        new_code_gate = block.find("_APPROVE_NON_BURNING_CODES")
        record_at = block.find("_record_mismatch(")
        assert new_code_gate != -1, "the new-code gate is missing from 4f"
        assert record_at != -1, "the mismatch branch lost its recording"
        assert new_code_gate < record_at, (
            "the Phase-1.5 codes must refuse BEFORE the counter is touched"
        )

    def test_the_three_genuine_mismatch_sites_still_record(self) -> None:
        """4c-item / 4d / 4e keep ``_record_mismatch`` (untouched)."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert source.count("await _record_mismatch(token_repo, token_row)") == 4, (
            "expected the three genuine mismatch sites + the pre-existing "
            "4f mismatch branch"
        )

    def test_the_mint_flags_carry_mismatch_zero_on_new_codes(self) -> None:
        """T2.17 (blocker #6): the mint-side new-code envelope states
        ``mismatch=0`` directly (the mint path bypasses the budget helper)."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert 'flags={"private": False, "mismatch": False}' in source

    def test_the_dead_is_group_dm_slot_is_gone(self) -> None:
        """T2.16 (P2-AC16): ``_AssentContext`` no longer carries the second
        carrier — the classification reads the raw meta dict."""
        import ast
        import textwrap

        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        cls = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "_AssentContext"
        )
        seg = textwrap.dedent(ast.get_source_segment(source, cls) or "")
        slots_at = seg.find("__slots__")
        slots_block = seg[slots_at : seg.find(")", slots_at)]
        assert "is_group_dm" not in slots_block, (
            "_AssentContext still carries the dead is_group_dm slot"
        )

    def test_the_map_digest_bypasses_the_flags_renderer(self) -> None:
        """DIGEST SCOPE (D-15(b) / AC13): ``map_digest`` is a dedicated
        field, emitted only when supplied — the ``'1'/'0'`` coercion is
        untouched."""
        import ast

        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        emit = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_emit_info_line"
        )
        seg = ast.get_source_segment(source, emit) or ""
        assert "map_digest: Optional[str] = None" in seg
        assert '" map_digest=%s"' in seg
        # The flags renderer is byte-unchanged.
        assert "'1' if v else '0'" in seg, (
            "the flags renderer's boolean coercion was touched — it must stay "
            "byte-unchanged (T2.11 / architect blocker #4)"
        )

    def test_the_dm_mint_passes_no_map_digest(self) -> None:
        """INVARIANT-DM: a DM mint emits NO ``map_digest`` — the value is
        ``mint_digest``, which the DM short-circuit leaves as ``None``."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert "map_digest=mint_digest," in source
        # ``mint_digest`` is only assigned inside the designated branch.
        assert source.count("mint_digest = designations.digest") == 1

    def test_the_non_dm_external_id_is_audit_only(self) -> None:
        """D-19: ``_parse_external_user_id`` is NEVER applied to the derived
        composite, and the derivation has no in-tree consumer."""
        # The derivation site.
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert 'return f"channel:{guild_id.strip()}:{channel_id.strip()}:{user_id}"' in source
        # ``_parse_external_user_id`` lives on the ADAPTER only. The assent
        # module mentions the SYMBOL in its D-19 docstring (declaring it
        # never applied) — but CALLS it nowhere and imports it never.
        assent_src = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert "_parse_external_user_id(" not in assent_src
        assert "import _parse_external_user_id" not in assent_src
        assert "from daemon.sources.adapters.discord" not in assent_src
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "_parse_external_user_id(" not in text:
                continue
            assert path.name == "adapter.py", (
                f"{path} CALLS _parse_external_user_id — the channel "
                "composite is AUDIT-ONLY (D-19)"
            )

    def test_the_designation_log_line_has_no_boot_hook(self) -> None:
        """D-20: first-resolution + on-digest-change INSIDE the resolver; no
        boot hook, no ``daemon/api.py`` edit."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert "first_resolution=" in source
        assert "digest_changed=" in source
        api = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
        assert "ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS" not in api
        assert "_resolve_designations" not in api


class TestThePhase1_5RuntimeContract:
    """P2's own acceptance criteria, asserted by test (T2.14 / T2.10-T2.12 /
    T2.15 / PR-1). The full cell matrix lives in Phase 3's file; these are the
    properties Phase 2's edits introduce or must not disturb.
    """

    CHANNEL = "111122223333444455"
    APPROVER = "112233445566"

    @staticmethod
    def _guild_meta(user_id: str, channel_id: str = "111122223333444455"):
        from tests.helpers.prompt_assent_tool_fixture import build_discord_meta

        return build_discord_meta(
            is_dm=False,
            is_group_dm=False,
            guild_id="777",
            channel_id=channel_id,
            channel_type="text",
            user_id=user_id,
        )

    @staticmethod
    def _info_lines(caplog) -> list[str]:
        return [
            rec.getMessage()
            for rec in caplog.records
            if rec.name == "daemon.tools.prompt_assent"
            and rec.getMessage().startswith("prompt_assent ")
        ]

    async def test_the_designated_mint_flag_set(self, monkeypatch, caplog) -> None:
        """P2-AC15 — a designated-channel mint emits
        ``private=False, private_equivalent=True`` and carries the map digest;
        a DM mint emits ``private=True, private_equivalent=True`` and NO
        digest (AC13 / INVARIANT-DM)."""
        import json
        import logging

        from daemon.tools.prompt_assent import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
            KILL_SWITCH_ENV,
        )
        from tests.helpers.prompt_assent_flow import build_flow, mint_for

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
            json.dumps({self.CHANNEL: self.APPROVER}),
        )
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            flow = build_flow(meta=self._guild_meta(self.APPROVER))
            mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        lines = self._info_lines(caplog)
        outcome_line = next(l for l in lines if "action=mint" in l and "outcome=ok" in l)
        assert "private=0" in outcome_line, outcome_line
        assert "private_equivalent=1" in outcome_line, outcome_line
        assert "map_digest=" in outcome_line, outcome_line
        digest = outcome_line.rsplit("map_digest=", 1)[1].strip()
        assert len(digest) == 12 and int(digest, 16) >= 0
        # The composite is AUDIT-ONLY and names the channel + human.
        row = flow.token_repo.find_live(flow.version_id)
        assert row.mint_is_dm == 0
        assert row.mint_channel_id == self.CHANNEL
        assert row.mint_user_id == self.APPROVER
        assert row.minted_external_user_id == (
            f"channel:777:{self.CHANNEL}:{self.APPROVER}"
        )

        # A DM mint: private=True and NO map_digest at all.
        caplog.clear()
        monkeypatch.setenv(
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
            json.dumps({self.CHANNEL: self.APPROVER}),
        )
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            dm_flow = build_flow()
            await mint_for(dm_flow)
        dm_line = next(
            l for l in self._info_lines(caplog)
            if "action=mint" in l and "outcome=ok" in l
        )
        assert "private=1" in dm_line, dm_line
        assert "private_equivalent=1" in dm_line, dm_line
        assert "map_digest" not in dm_line, (
            "a DM mint must record NO map_digest — the DM path never reads "
            "the designation variable (INVARIANT-DM)"
        )

    async def test_the_audit_payload_digest_scope(self, monkeypatch) -> None:
        """AC14 / D-15(b) — the decision audit payload carries ``map_digest``
        ONLY for a channel-minted token; a DM decision carries none."""
        import json

        from daemon.tools.prompt_assent import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
            KILL_SWITCH_ENV,
        )
        from tests.helpers.prompt_assent_flow import (
            build_flow,
            decide,
            echo,
            mint_for,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
            json.dumps({self.CHANNEL: self.APPROVER}),
        )

        def _last_payload(flow):
            from sqlmodel import Session, col, select

            from daemon.repositories.prompt_version.models import (
                PromptAuditEvent,
            )

            with Session(flow.engine) as session:
                rows = session.exec(
                    select(PromptAuditEvent).order_by(
                        col(PromptAuditEvent.created_at)
                    )
                ).all()
            return rows[-1].payload

        flow = build_flow(meta=self._guild_meta(self.APPROVER))
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        echo(
            flow,
            token,
            is_dm=False,
            is_group_dm=False,
            guild_id="777",
            channel_id=self.CHANNEL,
            channel_type="text",
            user_id=self.APPROVER,
        )
        assert "prompt_assent approve — OK" in await decide(flow, token=token)
        payload = _last_payload(flow)
        assert "map_digest" in payload
        digest = payload["map_digest"]
        assert len(digest) == 12 and int(digest, 16) >= 0
        # The DIGEST is content-free (the payload's OTHER keys carry the
        # ledger's channel/user provenance by design — unchanged, D-9).
        assert self.CHANNEL not in digest
        assert self.APPROVER not in digest

        dm_flow = build_flow()
        dm_mint = await mint_for(dm_flow)
        dm_token = dm_mint.split("token: ", 1)[1].splitlines()[0]
        echo(dm_flow, dm_token)
        assert "prompt_assent approve — OK" in await decide(
            dm_flow, token=dm_token
        )
        assert "map_digest" not in _last_payload(dm_flow), (
            "a DM decision must carry NO map_digest"
        )

    async def test_the_dm_path_never_resolves_the_map(self, monkeypatch) -> None:
        """PR-1 — the runtime resolver-raises pin. With the resolver
        MONKEYPATCHED TO RAISE, a DM mint, a DM approve and a lane-disabled
        call all complete with no raise (the primary, non-vacuous proof of
        INVARIANT-DM)."""
        import daemon.tools.prompt_assent as pa
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV
        from tests.helpers.prompt_assent_flow import (
            build_flow,
            decide,
            echo,
            mint_for,
        )

        def _boom():
            raise AssertionError("the DM path reached the designation resolver")

        monkeypatch.setattr(pa, "_resolve_designations", _boom)

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        echo(flow, token)
        assert "prompt_assent approve — OK" in await decide(flow, token=token)

        # The lane-disabled call takes the earliest exit — no map either.
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        disabled = await mint_for(build_flow())
        assert "reason=lane-disabled" in disabled, disabled

    async def test_the_new_codes_do_not_burn_the_token(self, monkeypatch) -> None:
        """THE 4f SPLIT, behaviourally (T2.5 / AC7 / A-9 + A-10): after an
        ``approver-not-pinned`` and after a ``designation-config-invalid``
        refusal the token's ``attempts`` is UNCHANGED, the token stays
        ``live`` and no revoke fires."""
        import json

        from daemon.tools.prompt_assent import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS as ENV,
            KILL_SWITCH_ENV,
        )
        from tests.helpers.prompt_assent_flow import (
            build_flow,
            decide,
            echo,
            mint_for,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(ENV, json.dumps({self.CHANNEL: self.APPROVER}))

        # (a) re-designation between mint and approve
        flow = build_flow(meta=self._guild_meta(self.APPROVER))
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        monkeypatch.setenv(ENV, json.dumps({self.CHANNEL: "555666777888999000"}))
        echo(
            flow, token, is_dm=False, is_group_dm=False, guild_id="777",
            channel_id=self.CHANNEL, channel_type="text", user_id=self.APPROVER,
        )
        result = await decide(flow, token=token)
        assert "reason=approver-not-pinned" in result, result
        after = flow.token_repo.find_by_id(live.token_id)
        assert after.attempts == 0, "the 4f split must not touch the counter"
        assert after.status == "live", "the token must not be revoked"
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"

        # (b) an invalid map at decision time
        monkeypatch.setenv(ENV, json.dumps({self.CHANNEL: self.APPROVER}))
        flow = build_flow(meta=self._guild_meta(self.APPROVER))
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        monkeypatch.setenv(ENV, "{bad")
        echo(
            flow, token, is_dm=False, is_group_dm=False, guild_id="777",
            channel_id=self.CHANNEL, channel_type="text", user_id=self.APPROVER,
        )
        result = await decide(flow, token=token)
        assert "reason=designation-config-invalid" in result, result
        after = flow.token_repo.find_by_id(live.token_id)
        assert after.attempts == 0
        assert after.status == "live"

    async def test_a_withdrawn_designation_still_burns(self, monkeypatch) -> None:
        """A-12 (LEADER-RULED asymmetry): a withdrawn designation is a
        USER-side mismatch mapping to ``token-channel-mismatch`` — it BURNS.
        The no-burn carve-out is only for the two NEW codes."""
        import json

        from daemon.tools.prompt_assent import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS as ENV,
            KILL_SWITCH_ENV,
        )
        from tests.helpers.prompt_assent_flow import (
            build_flow,
            decide,
            echo,
            mint_for,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(ENV, json.dumps({self.CHANNEL: self.APPROVER}))
        flow = build_flow(meta=self._guild_meta(self.APPROVER))
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        monkeypatch.setenv(ENV, "{}")  # the channel is no longer designated
        echo(
            flow, token, is_dm=False, is_group_dm=False, guild_id="777",
            channel_id=self.CHANNEL, channel_type="text", user_id=self.APPROVER,
        )
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result
        after = flow.token_repo.find_by_id(live.token_id)
        assert after.attempts == 1, "A-12 keeps the burn (D-17)"

    def test_the_shared_mint_detail_string_is_byte_identical(self) -> None:
        """T2.8 / D-2.3 / architect §2.4 — the pre-existing
        ``mint-requires-private-channel`` detail string is BYTE-IDENTICAL to
        v0.29.18, which is what keeps the DM cells' envelopes unchanged."""
        from daemon.tools.prompt_assent import _MINT_NOT_PRIVATE_DETAIL

        assert _MINT_NOT_PRIVATE_DETAIL == (
            "the mint context is not a proven 1:1 DM (is_dm AND NOT "
            "is_group_dm; an absent discriminator is not private) — the "
            "mint is refused and NO token row is written"
        )

    async def test_a_dm_mint_envelope_is_byte_identical(self, monkeypatch) -> None:
        """The whole-DM-envelope pin: an undesignated surface's refusal reads
        exactly the v0.29.18 code + detail, and the flags dict has NO new
        key."""
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV
        from tests.helpers.prompt_assent_flow import build_flow, mint_for
        from tests.helpers.prompt_assent_tool_fixture import build_discord_meta

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.delenv(
            "ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS", raising=False
        )
        flow = build_flow(meta=build_discord_meta(is_dm=True, is_group_dm=True))
        result = await mint_for(flow)
        assert result == (
            "Error: prompt_assent REFUSED — "
            "reason=mint-requires-private-channel: "
            "the mint context is not a proven 1:1 DM (is_dm AND NOT "
            "is_group_dm; an absent discriminator is not private) — the "
            "mint is refused and NO token row is written"
        ), result

    def test_the_dm_external_id_arm_is_byte_behaviour_unchanged(self) -> None:
        """AC17 — the DM arm still returns ``dm:<user_id>`` and nothing else
        changed about it; the non-DM arm needs a guild AND a channel AND the
        human (an absent nested user id yields ``None`` → M-19)."""
        from daemon.tools.prompt_assent import _derive_external_user_id
        from tests.helpers.prompt_assent_tool_fixture import FakeMessageRow

        row = FakeMessageRow()
        assert _derive_external_user_id(None, row, {}, "42", True) == "dm:42"
        assert _derive_external_user_id(None, row, {}, "", True) is None
        # A guild turn with a guild + channel + human derives the composite.
        meta = {"guild_id": "7", "channel_id": "8"}
        assert (
            _derive_external_user_id(None, row, meta, "9", False)
            == "channel:7:8:9"
        )
        # An absent human or an absent guild/channel yields None (fail closed).
        assert _derive_external_user_id(None, row, meta, "", False) is None
        assert _derive_external_user_id(None, row, {"channel_id": "8"}, "9", False) is None
        assert _derive_external_user_id(None, row, {"guild_id": "7"}, "9", False) is None

    def test_the_effective_map_line_is_first_resolution_and_digest_change(
        self, monkeypatch, caplog
    ) -> None:
        """AC15 / D-20 — the effective-map line fires at the FIRST successful
        resolution and again only when the digest changes; it is
        content-free."""
        import json
        import logging

        import daemon.tools.prompt_assent as pa
        from daemon.tools.prompt_assent import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS as ENV,
        )

        pa._reset_designation_log_state()
        monkeypatch.setenv(ENV, json.dumps({self.CHANNEL: self.APPROVER}))
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            first = pa._resolve_designations()
            second = pa._resolve_designations()  # same map — no NEW line flag
            monkeypatch.setenv(ENV, json.dumps({self.CHANNEL: "555666777888999000"}))
            third = pa._resolve_designations()
        assert first.valid and second.valid and third.valid
        lines = [
            rec.getMessage() for rec in caplog.records
            if rec.getMessage().startswith("prompt_assent designation_map")
        ]
        assert len(lines) == 3
        assert "first_resolution=1" in lines[0]
        assert "first_resolution=0" in lines[1] and "digest_changed=0" in lines[1]
        assert "digest_changed=1" in lines[2]
        for line in lines:
            assert self.CHANNEL not in line
            assert self.APPROVER not in line
        pa._reset_designation_log_state()

    def test_an_invalid_map_never_raises_and_logs_by_key(
        self, monkeypatch, caplog
    ) -> None:
        """PR-1's other half / AC1 — an invalid map resolves to a TYPED
        outcome (no exception) and logs one WARNING naming the KEY."""
        import logging

        import daemon.tools.prompt_assent as pa
        from daemon.tools.prompt_assent import (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS as ENV,
        )

        pa._reset_designation_log_state()
        monkeypatch.setenv(ENV, '{"9" : "not-an-id"}')
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            result = pa._resolve_designations()
        assert result.valid is False
        warnings = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.WARNING
            and rec.name == "daemon.tools.prompt_assent"
        ]
        assert any('"9"' in w or "9" in w for w in warnings)
        assert not any("not-an-id" in w for w in warnings), (
            "the warning must never echo the operator's VALUE"
        )
        pa._reset_designation_log_state()


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
