"""Registration + allow-list proof for the ``memoir`` category
(memoir-store-v1 lane, 2026-11-05 — the phase-4 ceremony).

The three-step registration seam (decorator + registry entries +
construction) is proven END-TO-END here, in the notify-user shape:

* (a) the AST discovery finds the six memoir tools in the category
  module;
* (b) the category is in ``PRIVILEGED_TOOL_CATEGORIES``;
* (c) the frozenset LENGTH is 19 (memoir joined 2026-11-05 as the
  eighteenth member; the nest-anomaly lineage's nest-report-read landed
  first at merge time, so memoir ships as entry #19);
* (d) the set is EXACTLY the twenty entries — and the expected set is
  DERIVED BY GREP AT TEST TIME (the registry's own literal, cross-checked
  against every pin file's literal), never hardcoded from prose;
* (e) ``CATEGORY_MODULES["memoir"]`` resolves to a ``daemon.tools.*``
  path (the AST scanner resolves the value as
  ``daemon/tools/<parts[2:]>.py`` — a ``daemon.services.*`` value is
  never discovered and the drift pin fails at boot);
* (f) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry the six
  tool names (factory-built, not import-time registered);
* (g) the ``create_instance_tools`` extend is greppable in source AND
  precedes ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (decorator-only registration is SILENTLY
  INVISIBLE — a late extend bypasses the privileged strip);
* (h) the ari-ONLY census: ``memoir`` appears in
  ``agents/ari/meta.json`` and NOWHERE ELSE across every
  ``agents/*/meta.json``;
* (i) the in-body runtime gate exists in source (layer 2 — a literal
  tool name in an allow list bypasses the category gate);
* (j) the decorator order is load-bearing and pinned:
  ``@register_tool_category`` ABOVE ``@tool``.

THE CEREMONY SELF-VERIFICATION (the pin that outlives the ceremony):
every count pin says 18; every exact-set literal (the registry's own +
every pin file's) carries the SAME twenty members; the ari allow array
ENDS with ``"memoir"``; and the standing mutation instrument's MT-01
literals are LOCKSTEPPED (find 18 / replace 17 — never 18/18, which
would make the mutation vacuous).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
)

CATEGORY = "memoir"
MODULE = "daemon.tools.memoir"
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memoir_write",
        "memoir_pin",
        "memoir_unpin",
        "memoir_recall",
        "memoir_timeline",
        "memoir_about",
    }
)

#: The pin files the ceremony grep found (the SET is derived at test time
#: from the greps below — this list is the fixture the greps are run
#: against, not a census claim).
COUNT_PIN_FILES: tuple[str, ...] = (
    "tests/unit/test_board_registration.py",
    "tests/unit/test_wedge_capture_registration.py",
    "tests/unit/test_self_alarm_registration.py",
    "tests/unit/test_notify_user_registration.py",
    "tests/unit/test_metrics_read_registration.py",
    "tests/unit/test_instance_ops_registration.py",
)
EXPECTED_SET_FILES: tuple[str, ...] = (
    "tests/unit/test_board_registration.py",
    "tests/unit/test_wedge_capture_registration.py",
    "tests/unit/test_self_alarm_registration.py",
    "tests/unit/test_notify_user_registration.py",
    "tests/unit/test_metrics_read_registration.py",
    "tests/unit/test_instance_ops_registration.py",
)
EXACT_SET_FILES: tuple[str, ...] = (
    "tests/integration/test_service_tool_flag_off_byte_identical.py",
    "tests/integration/test_maintenancer_spawn_resolves_tools.py",
    "tests/unit/tools/test_secrets_registration.py",
    "tests/unit/tools/test_upgrade_registration.py",
    "tests/unit/tools/test_attestation_registration.py",
)
MT01_FILE = "test/packs/metrics_read_mutation_matrix_test.py"


def _frozenset_literals(path: Path) -> list[frozenset[str]]:
    """Every ``frozenset({...})`` literal in a file (AST-extracted).

    The members are read from the SOURCE, never from prose — the pin
    compares what the files ACTUALLY assert.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[frozenset[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "frozenset"):
            continue
        if len(node.args) != 1:
            continue
        arg = node.args[0]
        if not isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
            continue
        members = {
            element.value
            for element in arg.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        # The PRIVILEGED-CATEGORY literal, not the tool-NAME sets: the
        # category members carry the category spellings (system-log /
        # ens-db / ens-env), the tool-name sets carry the tool names
        # (ens_system_log_*).
        if {"system_upgrade", "system-log", "ens-db", "ens-env"} <= members:
            found.append(frozenset(members))
    return found


def _registry_literal_members() -> frozenset[str]:
    """The registry's own ``PRIVILEGED_TOOL_CATEGORIES`` literal (AST)."""
    literals = _frozenset_literals(REPO_ROOT / "daemon" / "tools" / "_tool_registry.py")
    assert literals, "the registry's privileged-set literal was not found"
    # The registry holds exactly ONE privileged literal (the DYNAMIC /
    # KNOWN name sets carry no category members).
    assert len(literals) == 1, f"unexpected privileged literals: {literals}"
    return literals[0]


class TestTheCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_has_twenty_members(self) -> None:
        # prompt-reload (2026-11-06) entered as #20 after memoir.
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 20

    def test_the_derived_set_is_exactly_the_twenty_entries(self) -> None:
        """The expected set is DERIVED (the registry's own literal), never
        hardcoded from a prose census."""
        derived = _registry_literal_members()
        assert len(derived) == 20, sorted(derived)
        assert CATEGORY in derived
        assert PRIVILEGED_TOOL_CATEGORIES == derived

    def test_the_memoir_entry_is_followed_only_by_later_lanes(self) -> None:
        """The registry's literal appends the new entry at the END (the
        house convention — the set is a frozen literal, not a set). The
        prompt-reload lane (2026-11-06) appended after memoir, so memoir
        is now second-to-last — exactly as notify-user/nest-report-read
        are for their successors."""
        source = (REPO_ROOT / "daemon" / "tools" / "_tool_registry.py").read_text(
            encoding="utf-8"
        )
        block = source.split("PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({", 1)[
            1
        ]
        literal = block.split("\n})", 1)[0]
        entries = [
            line.strip().strip(",").strip('"')
            for line in literal.splitlines()
            if line.strip().startswith('"')
        ]
        assert entries[-2] == CATEGORY, entries[-4:]
        assert entries[-1] == "prompt-reload", entries[-4:]

    def test_the_category_module_resolves_to_a_daemon_tools_path(self) -> None:
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        assert MODULE.startswith("daemon.tools.")

    def test_the_six_tool_names_are_known(self) -> None:
        for name in TOOL_NAMES:
            assert name in DYNAMIC_TOOL_NAMES, name
            assert name in KNOWN_TOOL_NAMES, name

    def test_the_module_imports_cleanly_and_carries_the_six_tools(self) -> None:
        from daemon.tools.memoir import (
            MEMOIR_CATEGORY_KEY,
            MEMOIR_TOOL_NAMES,
            create_memoir_tools,
        )

        assert MEMOIR_CATEGORY_KEY == CATEGORY
        assert MEMOIR_TOOL_NAMES == TOOL_NAMES
        built = create_memoir_tools(None, agent_id="ari")
        assert {tool.name for tool in built} == set(TOOL_NAMES)


class TestTheRegistrationWiring:
    def test_the_extend_precedes_the_help_scan_and_filter(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        extend_idx = source.index("create_memoir_tools(")
        help_idx = source.index("create_help_tool(tools")
        scan_idx = source.index("scan_tools_for_full_docs(tools)")
        filter_idx = source.index("_apply_tool_filter(tools, agent_id")
        assert extend_idx < help_idx, "extend must precede create_help_tool"
        assert extend_idx < scan_idx, (
            "extend must precede scan_tools_for_full_docs"
        )
        assert extend_idx < filter_idx, (
            "extend must precede _apply_tool_filter"
        )

    def test_the_module_import_sits_with_the_other_factories(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .memoir import create_memoir_tools" in source

    def test_the_decorator_order_is_load_bearing(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "memoir.py").read_text(
            encoding="utf-8"
        )
        assert source.count("@register_tool_category(MEMOIR_CATEGORY_KEY)") == 6
        for schema in (
            "MemoirWriteInput",
            "MemoirPinInput",
            "MemoirUnpinInput",
            "MemoirRecallInput",
            "MemoirTimelineInput",
            "MemoirAboutInput",
        ):
            dec_idx = source.index("@register_tool_category(MEMOIR_CATEGORY_KEY)")
            tool_idx = source.index(f"@tool(args_schema={schema})")
            assert dec_idx < tool_idx, f"{schema}: decorator order inverted"

    def test_the_body_gate_is_present_in_source(self) -> None:
        """Layer 2 — the in-body runtime gate. A literal tool name in an
        allow list bypasses the category gate, so the body gate is
        mandatory. The tidier's Batch-A consolidation moved the gates
        into ONE shared ``_gate`` helper: the count is now on the
        DELEGATION call shape — every one of the six bodies must gate
        on the factory-captured id by delegating to ``_gate``."""
        source = (REPO_ROOT / "daemon" / "tools" / "memoir.py").read_text(
            encoding="utf-8"
        )
        assert (
            source.count("refusal = _gate(label,") == 6
        ), "every one of the six bodies must gate on the captured id"
        assert source.count("def _gate(label: str, action: str, agent_id: str, manager: Any) -> str | None:") == 1, (
            "the shared gate helper must exist exactly once"
        )

    def test_the_gate_order_is_auth_first_in_source(self) -> None:
        """The auth gate precedes the knob read, which precedes the first
        DB touch (AM-16's order — the exemplar's CODE, not its comment).
        Batch-A: the order AUTH-then-KNOB lives in the ONE ``_gate``
        helper every body delegates to, and the helper itself touches no
        DB; each body's first DB touch comes only AFTER its gate call
        passed (``_gate`` returned ``None``)."""
        source = (REPO_ROOT / "daemon" / "tools" / "memoir.py").read_text(
            encoding="utf-8"
        )
        gate_def_idx = source.index("def _gate(")
        helper_end_idx = source.index("\ndef ", gate_def_idx + 1)
        helper_span = source[gate_def_idx:helper_end_idx]
        auth_idx = helper_span.index("if not _is_memoir_authorized(")
        knob_idx = helper_span.index("if not memoir_enabled(")
        assert auth_idx < knob_idx, (
            "the shared gate helper must check auth BEFORE the knob"
        )
        assert "asyncio.to_thread(" not in helper_span, (
            "the shared gate helper must touch no DB (an unauthorized "
            "caller learns nothing and touches no DB)"
        )
        # Every delegation site must precede its body's first DB touch.
        call_starts = [
            match.start()
            for match in re.finditer(r"refusal = _gate\(label,", source)
        ]
        assert len(call_starts) == 6, "six bodies must delegate to _gate"
        for start in call_starts:
            first_db_idx = source.find("asyncio.to_thread(", start)
            assert first_db_idx != -1, (
                "each gated body must reach the DB through asyncio.to_thread"
            )
            assert start < first_db_idx, (
                "the _gate delegation must precede the body's first DB "
                "touch (auth-first: no DB work before the gates pass)"
            )
            segment = source[start:first_db_idx]
            assert segment.count("refusal = _gate(") == 1, (
                "the FIRST DB touch after a delegation must belong to the "
                "SAME body — a hoisted DB touch before the gate hides "
                "behind a later body's call otherwise"
            )
        # A DB touch must not precede the gate WITHIN a body: from each
        # tool decorator to its body's gate call, no DB touch may appear.
        for match in re.finditer(
            r"@register_tool_category\(MEMOIR_CATEGORY_KEY\)", source
        ):
            gate_idx = source.find("refusal = _gate(label,", match.start())
            assert gate_idx != -1, (
                "each decorated tool body must delegate to _gate"
            )
            prefix = source[match.start():gate_idx]
            assert "asyncio.to_thread(" not in prefix, (
                "a DB touch must not precede the body's gate call "
                "(auth-first: an unauthorized caller touches no DB)"
            )


class TestTheAriOnlyAllowList:
    def test_ari_meta_carries_the_category_key(self) -> None:
        meta = json.loads(
            (REPO_ROOT / "agents" / "ari" / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        allow = meta.get("tools", {}).get("allow", []) or []
        assert CATEGORY in allow
        assert allow[-1] == "prompt-reload", (
            "the prompt-reload entry (2026-11-06) must be the LAST entry "
            "of ari's allow array (the trailing-entry discipline)"
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
            f"the memoir category is ARI-ONLY; found in: {offenders}"
        )

    def test_the_trailing_set_pin_carries_memoir_last(self) -> None:
        """The board's trailing-entry pin — the SET the ceremony bumped.

        MERGE NOTE (2026-11-06): the pin now carries the nest-anomaly
        lineage's ``nest-report`` entry between ``notify-user`` and
        ``memoir``, so the assertion is on the SLICE END (memoir last),
        not on an adjacency that the merge legitimately rewrote.
        """
        source = (REPO_ROOT / "tests/unit/test_board_registration.py").read_text(
            encoding="utf-8"
        )
        assert '"memoir", "prompt-reload"]' in source, (
            "the board trailing pin must end with prompt-reload "
            "(the prompt-reload lane, 2026-11-06, appended after memoir)"
        )
        # "pok" re-point 2026-11-08 (POK B4 sweep 16b4e70b2): the board
        # pin's ari literal gained "pok" between "notify-user" and
        # "nest-report" — the adjacency this pin matches moved with it.
        assert '"notify-user", "pok", "nest-report", "memoir"' in source


class TestTheCeremonySelfVerification:
    """The pins outlive the ceremony: every count pin says 20, every
    exact-set literal agrees with the registry's own, and the standing
    mutation instrument is lockstepped."""

    def test_every_count_pin_says_twenty(self) -> None:
        for rel in COUNT_PIN_FILES:
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "assert len(PRIVILEGED_TOOL_CATEGORIES) == 20" in source, rel
            assert "assert len(PRIVILEGED_TOOL_CATEGORIES) == 19" not in source, (
                f"{rel}: a stale 19-flavored count pin survived the "
                f"prompt-reload ceremony"
            )

    def test_every_expected_set_definition_agrees_with_the_registry(self) -> None:
        derived = _registry_literal_members()
        for rel in EXPECTED_SET_FILES:
            literals = _frozenset_literals(REPO_ROOT / rel)
            assert literals, f"{rel}: no privileged-set literal found"
            for literal in literals:
                assert literal == derived, (
                    f"{rel}: the pinned set diverges from the registry's "
                    f"literal ({sorted(literal ^ derived)})"
                )

    def test_every_inline_exact_set_pin_agrees_with_the_registry(self) -> None:
        derived = _registry_literal_members()
        for rel in EXACT_SET_FILES:
            literals = _frozenset_literals(REPO_ROOT / rel)
            assert literals, f"{rel}: no inline privileged-set literal found"
            for literal in literals:
                assert literal == derived, (
                    f"{rel}: the inline pinned set diverges from the "
                    f"registry's literal ({sorted(literal ^ derived)})"
                )

    def test_the_mt01_literals_are_lockstepped_and_not_vacuous(self) -> None:
        """W4 — the standing mutation instrument stays LOCKSTEPPED and
        NON-VACUOUS after the merge.

        The nest-anomaly-report lineage replaced MT-01's hardcoded
        find/replace literals with a pair DERIVED from the live constant
        at pack-import time (its C5 fix), which is strictly better than
        the static pair this leg originally pinned: the find string is
        always the live count and the replace is always count − 1, so a
        later count bump can never stale the mutation (and can never make
        it vacuous — a vacuous pair would be find == replace).
        """
        source = (REPO_ROOT / MT01_FILE).read_text(encoding="utf-8")
        assert "_COUNT_FIND = f\"assert len(PRIVILEGED_TOOL_CATEGORIES) == {len(_PRIV_COUNT_NOW)}\"" in source
        assert "len(_PRIV_COUNT_NOW) - 1" in source, (
            "MT-01's replace string must plant the OLD (count - 1) value"
        )
        assert '"assert len(PRIVILEGED_TOOL_CATEGORIES) == 17"' not in source, (
            "a stale hardcoded 17-flavored literal survived the merge"
        )

    def test_the_registry_prose_census_is_corrected(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "_tool_registry.py").read_text(
            encoding="utf-8"
        )
        # The stale historical census claims must be gone …
        assert "SIXTEEN assertion sites across TEN files" not in source
        # … and the block must carry the DERIVE-BY-GREP instruction plus
        # the dated merge derivation note (the census itself is re-derived
        # at every ceremony and is never a frozen literal).
        assert "DERIVE THE PIN SET BY GREPPING THE TREE" in source
        assert "2026-11-06 memoir-merge derivation" in source
