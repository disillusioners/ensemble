"""Registration + allow-list + parser proof for the ``guidelines``
category (guideline-read-tool lane, 2026-11-13 — phases 1+2).

The three-step registration seam (decorator + registry entries +
construction) is proven END-TO-END here, in the notify-user shape:

* (a) the AST discovery finds both tools in the category module, and
  the constructed tools carry ``_tool_category == "guidelines"`` and
  the first-party provenance marker;
* (b) the decorator order is load-bearing and pinned:
  ``@register_tool_category`` ABOVE ``@tool``;
* (c) ``CATEGORY_MODULES["guidelines"]`` resolves to a
  ``daemon.tools.*`` path (the AST scanner resolves the value as
  ``daemon/tools/<parts[2:]>.py`` — a ``daemon.services.*`` value is
  never discovered and the drift pin fails at boot);
* (d) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry both names
  (factory-built, not import-time registered);
* (e) the ``create_instance_tools`` extend is greppable in source AND
  precedes ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (decorator-only registration is SILENTLY
  INVISIBLE — a late extend bypasses the privileged strip);
* (f) the category is PRIVILEGED entry #22 — membership + EXACT set +
  count 22;
* (g) the FOUR-AGENT allow census: ``"guidelines"`` appears in
  EXACTLY {ari, leader, devops, sonic-leader}'s ``tools.allow``
  arrays and in NO other agent's allow array (allow-array semantics —
  NOT a bare file grep: deny arrays are a live pattern);
* (h) ``GUIDELINES_AUTHORIZED_AGENTS`` is EXACTLY the four-agent
  frozenset;
* (i) the in-body runtime gate is the FIRST logic in each tool body
  (a literal tool name in an allow list bypasses the category gate —
  and a refused caller must never touch the filesystem);
* (j) the doctrine renders: CATEGORY_DOC + ``_full_doc_`` carry BOTH
  fixed doctrine lines VERBATIM, and the loader warm-list entry exists
  so cold-boot docs render the category (W1-FIX-NOW class).

Plus the B-matrix legs assigned to phases 1+2 (B4 auth-first typed
refusal BEFORE any file IO; B5 the ``midflight`` negative-claim guard)
and the C-matrix parser legs (C1–C7: happy parse against the repo
``docs/guidelines`` via ``base_dir`` injection, fail-soft synthetic
fixtures, typed refusals, verbatim round-trip, unknown-slug refusal,
write-path absence).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)

CATEGORY = "guidelines"
MODULE = "daemon.tools.guidelines"
TOOL_NAMES: frozenset[str] = frozenset({"guidelines_list", "guideline_read"})

#: The FOUR agents the grant covers — the FIRST privileged grant to a
#: sonic agent is sonic-leader (ruling 2026-11-13).
GRANTED_AGENTS = ("ari", "leader", "devops", "sonic-leader")

#: The doctrine lines — VERBATIM, in docstring + CATEGORY_DOC +
#: ``_full_doc_`` (the commission's fixed text; a paraphrase anywhere
#: is a defect).
DOCTRINE_LINE_1 = "Fixed files are the authority; this tool only reads them"
DOCTRINE_LINE_2 = (
    "Every feature shipping dormant / config-armed / ceremony-gated / "
    "with silently-biting rules OWES a guideline (see "
    "docs/guidelines/TEMPLATE.md)"
)
RELEASE_PAYLOAD_NOTE = (
    "cannot serve any guideline newer than the running release"
)

EXPECTED_PRIVILEGED_SET: frozenset[str] = frozenset({
    "system_upgrade",
    "system-log",
    "ens-db",
    "ens-env",
    "view-views",
    "secrets",
    "settings",
    "board",
    "self-alarm",
    "view-write",
    "board-link",
    "wedge-capture",
    "instance_read",
    "instance_ops",
    "mcp_rpc",
    "notify-user",
    "metrics-read",
    "nest-report-read",
    "memoir",
    "prompt-reload",
    # prompt-assent (2026-11-10, prompt-assent lane): privileged entry #21
    # (ari-exactly; see daemon/tools/_tool_registry.py).
    "prompt-assent",
    # guidelines (2026-11-13, guideline-read-tool lane): privileged entry
    # #22 — the FIRST privileged grant to a sonic agent (sonic-leader;
    # ruling 2026-11-13; see daemon/tools/_tool_registry.py).
    "guidelines",
})


def _module_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "guidelines.py").read_text(
        encoding="utf-8"
    )


def _instance_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
        encoding="utf-8"
    )


def _tools(agent_id: str = "ari", base_dir: Path | None = None) -> dict[str, object]:
    from daemon.tools.guidelines import create_guidelines_tools

    tools = create_guidelines_tools(
        None,
        current_instance_id="",
        agent_id=agent_id,
        agent_tag=None,
        base_dir=base_dir,
    )
    return {getattr(t, "name", ""): t for t in tools}


# ── A. the static registration points (notify legs a–j) ─────────────────


class TestStaticRegistration:
    def test_step1_ast_discovery_finds_both_tools(self) -> None:
        """Step 1 — the AST walker finds BOTH factory-created ``@tool``
        functions at their nesting depth inside the factory."""
        discovered = discover_source_only_tool_names()
        missing = TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_step1b_the_tools_carry_the_category_provenance(self) -> None:
        """The constructed tools carry ``_tool_category ==
        "guidelines"`` AND the first-party provenance marker (the
        rescan spoofing guard)."""
        tools = _tools(agent_id="ari", base_dir=REPO_ROOT / "docs" / "guidelines")
        assert set(tools) == set(TOOL_NAMES)
        for name, tool_obj in tools.items():
            assert getattr(tool_obj, "_tool_category", None) == CATEGORY, name
            assert getattr(tool_obj, "_tool_category_first_party", None) is True, name

    def test_step1c_the_module_ast_carries_the_decorator_order(self) -> None:
        """The decorator order is LOAD-BEARING: if ``@tool`` ran first,
        the wrapper would carry the category but the privileged strip
        reads the WRAPPED object — the order must be
        ``@register_tool_category`` ABOVE ``@tool``."""
        tree = ast.parse(_module_source())

        def _decorator_name(d: ast.expr) -> str | None:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name):
                return d.func.id
            if isinstance(d, ast.Name):
                return d.id
            return None

        orders: list[tuple[str, ...]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [
                    name
                    for name in (_decorator_name(d) for d in node.decorator_list)
                    if name is not None
                ]
                if "register_tool_category" in decorators:
                    orders.append(tuple(decorators))
        assert len(orders) == 2, f"expected BOTH tools decorated: {orders}"
        for order in orders:
            assert order == ("register_tool_category", "tool"), (
                f"decorator order drifted: {orders} — register_tool_category "
                "must sit ABOVE @tool"
            )

    def test_step2_category_modules_entry(self) -> None:
        """Step 2 — ``CATEGORY_MODULES['guidelines']`` points at the
        module, and it is a ``daemon.tools.*`` path."""
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        assert MODULE.startswith("daemon.tools."), (
            f"{MODULE} is not a daemon.tools.* path — a "
            "daemon.services.* value is never discovered by the "
            "AST scanner and the drift pin fails at boot"
        )

    def test_step3_dynamic_tool_names_contains_both(self) -> None:
        missing = TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_step3b_known_tool_names_contains_both(self) -> None:
        missing = TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_step4_construction_extends_present_in_source(self) -> None:
        """Step 4 — the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``. Decorator-only registration is
        SILENTLY INVISIBLE."""
        source = _instance_source()
        assert "from .guidelines import create_guidelines_tools" in source
        assert "create_guidelines_tools_if_available(" in source
        assert "tools.extend(" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to
        the help tool."""
        source = _instance_source()
        extend_idx = source.index("create_guidelines_tools_if_available(")
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


class TestPrivilegedCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_twenty_two_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_twenty_two_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 22


class TestTheFourAgentAllowList:
    """The FOUR-agent census — allow-array semantics (NOT a bare file
    grep: deny arrays are a live pattern and must not match)."""

    def _allow(self, agent_id: str) -> list[str]:
        meta = json.loads(
            (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        return (meta.get("tools") or {}).get("allow") or []

    def test_every_granted_agent_carries_the_exact_key(self) -> None:
        for agent_id in GRANTED_AGENTS:
            allow = self._allow(agent_id)
            assert CATEGORY in allow, (
                f"agents/{agent_id}/meta.json must carry the exact "
                f"category key {CATEGORY!r}"
            )
            assert allow.count(CATEGORY) == 1, (
                f"{agent_id}: the category appears MORE THAN ONCE"
            )

    def test_ari_carries_it_as_the_literal_last_element(self) -> None:
        """ari is the ONLY order-pinned allow array — guidelines must
        be the literal LAST element (the trailing-append discipline)."""
        allow = self._allow("ari")
        assert allow[-1] == CATEGORY, (
            f"ari: guidelines must be the LAST allow element, got tail "
            f"{allow[-3:]}"
        )

    def test_sonic_leader_deny_array_is_untouched(self) -> None:
        """The sonic-leader deny array carries exactly its three
        entries — the grant touches the ALLOW array only."""
        meta = json.loads(
            (REPO_ROOT / "agents" / "sonic-leader" / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        deny = (meta.get("tools") or {}).get("deny") or []
        assert deny == ["edit_file", "write_file", "image_save"], (
            f"sonic-leader deny array must stay untouched, got {deny}"
        )

    def test_no_other_agent_carries_the_category_in_allow(self) -> None:
        """The census — every agents/*/meta.json EXCEPT the four
        grantees must NOT carry ``guidelines`` in its ALLOW array."""
        offenders: list[str] = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            if meta_path.parent.name in GRANTED_AGENTS:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = (meta.get("tools") or {}).get("allow") or []
            if CATEGORY in allow:
                offenders.append(str(meta_path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            f"guidelines is granted to EXACTLY {GRANTED_AGENTS}; "
            f"found in: {offenders}"
        )

    def test_the_module_authorizes_exactly_the_four(self) -> None:
        """Layer 3 — the in-body runtime gate constant. A literal tool
        name in an allow list bypasses the category gate, so the body
        gate is mandatory."""
        from daemon.tools.guidelines import (
            GUIDELINES_AUTHORIZED_AGENTS,
            GUIDELINES_CATEGORY_KEY,
            GUIDELINES_TOOLS,
        )

        assert GUIDELINES_AUTHORIZED_AGENTS == frozenset(
            {"ari", "leader", "devops", "sonic-leader"}
        )
        assert GUIDELINES_CATEGORY_KEY == CATEGORY
        assert GUIDELINES_TOOLS == ("guidelines_list", "guideline_read")


class TestBodyGateInSource:
    def test_the_gate_is_the_first_logic_in_each_body(self) -> None:
        """The authority gate is the FIRST logic in EACH tool body —
        a refused caller never reaches the payload resolution or any
        file IO (the metrics ladder shape). Compared PER TOOL BODY
        span, not across the whole module."""
        source = _module_source()
        gates = [
            m.start()
            for m in re.finditer(
                r"if not _is_guidelines_authorized\(caller_agent_id\):", source
            )
        ]
        assert len(gates) == 2, f"expected BOTH tool bodies gated: {len(gates)}"
        starts = sorted(source.index(f"async def {tool}(") for tool in TOOL_NAMES)
        spans = [
            source[start: (starts[i + 1] if i + 1 < len(starts) else len(source))]
            for i, start in enumerate(starts)
        ]
        for span in spans:
            gate_idx = span.index(
                "if not _is_guidelines_authorized(caller_agent_id):"
            )
            first_io = span.index("_list_guideline_files(resolved_dir)")
            first_read = span.index("read_text(encoding=")
            assert gate_idx < first_io < first_read or gate_idx < first_read, (
                "the authority gate must precede payload access AND the "
                "file read inside the body"
            )
            assert gate_idx < first_io, (
                "the authority gate must precede the payload listing"
            )


class TestTheDoctrineRenders:
    """Leg (j) — the phrase-pin class: the doctrine lines render in
    CATEGORY_DOC, the module docstring and the ``_full_doc_`` surface,
    and the loader warm-list entry exists so COLD BOOT renders the
    category."""

    def test_the_module_docstring_carries_both_doctrine_lines(self) -> None:
        module = __import__("daemon.tools.guidelines", fromlist=["__doc__"])
        assert DOCTRINE_LINE_1 in (module.__doc__ or "")
        assert DOCTRINE_LINE_2 in (module.__doc__ or "")

    def test_category_doc_carries_both_doctrine_lines(self) -> None:
        from daemon.tools.guidelines import CATEGORY_DOC, CATEGORY_NAME

        assert CATEGORY_NAME == "Guidelines"
        assert DOCTRINE_LINE_1 in CATEGORY_DOC
        assert DOCTRINE_LINE_2 in CATEGORY_DOC
        assert "guidelines_list" in CATEGORY_DOC
        assert "guideline_read" in CATEGORY_DOC

    def test_full_doc_carries_both_lines_and_the_payload_note(self) -> None:
        tools = _tools(agent_id="ari", base_dir=REPO_ROOT / "docs" / "guidelines")
        for name, tool_obj in tools.items():
            doc = getattr(tool_obj, "_full_doc_", "")
            assert DOCTRINE_LINE_1 in doc, f"{name}: doctrine line 1 missing"
            assert DOCTRINE_LINE_2 in doc, f"{name}: doctrine line 2 missing"
            assert RELEASE_PAYLOAD_NOTE in doc, (
                f"{name}: the release-payload disclosure is missing"
            )

    def test_the_loader_warm_list_entry_exists(self) -> None:
        """Cold-boot docs render the category ONLY if the warm scan
        imports the module and scans its stub tools (W1-FIX-NOW
        class)."""
        source = (REPO_ROOT / "daemon" / "loader.py").read_text(
            encoding="utf-8"
        )
        assert "from .tools.guidelines import create_guidelines_tools" in source
        assert "guidelines_tools = create_guidelines_tools(None)" in source
        scan_idx = source.index("metrics_read_tools, nest_report_tools, nest_report_read_tools,")
        entry_idx = source.index("        guidelines_tools,")
        assert entry_idx > scan_idx, (
            "the warm-scan flattening tuple must carry guidelines_tools"
        )


# ── B. the behavior legs assigned to phases 1+2 ─────────────────────────


class TestBehaviorLegs:
    async def test_b4_auth_refusal_precedes_any_file_io(self) -> None:
        """B4 — a mismatched/unknown agent_id gets the typed AUTH
        refusal BEFORE any file IO: point ``base_dir`` at a NON-EXISTENT
        path — if the tool touched the payload first, the refusal would
        be the PAYLOAD token, not the AUTH token."""
        absent = REPO_ROOT / "docs" / "guidelines-does-not-exist"
        assert not absent.exists()
        for agent_id in ("sonic-coder", ""):
            tools = _tools(agent_id=agent_id, base_dir=absent)
            for name, tool_obj in tools.items():
                raw = await tool_obj.ainvoke({} if name == "guidelines_list" else {"slug": "x"})
                assert raw.startswith("Error:"), (agent_id, name, raw)
                assert "not-authorized" in raw or "unknown-agent" in raw, (
                    (agent_id, name, raw)
                )
                assert "guidelines-payload-unavailable" not in raw, (
                    f"{agent_id}/{name}: the payload was consulted "
                    f"BEFORE the gate — auth-first violated: {raw}"
                )

    async def test_b4_an_authorized_agent_on_the_same_absent_dir_gets_the_payload_refusal(self) -> None:
        """The complement — the SAME absent dir yields the typed
        payload refusal for an AUTHORIZED caller (never a raise, never
        an empty list), naming the resolved path."""
        absent = REPO_ROOT / "docs" / "guidelines-does-not-exist"
        tools = _tools(agent_id="ari", base_dir=absent)
        for name, tool_obj in tools.items():
            raw = await tool_obj.ainvoke({} if name == "guidelines_list" else {"slug": "x"})
            assert raw.startswith("Error:"), (name, raw)
            assert "guidelines-payload-unavailable" in raw, (name, raw)
            assert str(absent) in raw, f"{name}: the refusal must name the resolved path"

    def test_b5_midflight_did_not_confer_the_grant(self) -> None:
        """B5 — the negative-claim guard: sonic-leader holds
        ``midflight`` (NON-privileged, the cross-team QA channel) and
        that fact must NOT have conferred ``guidelines``. The midflight
        category is not a privileged member, its tool is disjoint from
        GUIDELINES_TOOLS, and the guidelines grant exists ONLY via the
        explicit allow entry (pinned by the census above)."""
        from daemon.tools.guidelines import GUIDELINES_TOOLS

        assert "midflight" not in PRIVILEGED_TOOL_CATEGORIES
        assert "guidelines_list" not in PRIVILEGED_TOOL_CATEGORIES
        assert "guideline_read" not in PRIVILEGED_TOOL_CATEGORIES
        source = (REPO_ROOT / "daemon" / "tools" / "midflight_report.py").read_text(
            encoding="utf-8"
        )
        assert "guidelines_list" not in source and "guideline_read" not in source, (
            "the midflight module must not carry guidelines tools"
        )
        assert not ({"midflight_report"} & set(GUIDELINES_TOOLS))
        # The sonic-leader allow entry for midflight exists INDEPENDENTLY
        # of the guidelines entry (the explicit grant is the only path).
        meta = json.loads(
            (REPO_ROOT / "agents" / "sonic-leader" / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        allow = (meta.get("tools") or {}).get("allow") or []
        assert "midflight" in allow and "guidelines" in allow


# ── C. the parser + tools legs (fail-soft contract) ─────────────────────


REPO_DOCS_GUIDELINES = REPO_ROOT / "docs" / "guidelines"
CANONICAL_SLUGS = (
    "guideline-live-views-passcode",
    "guideline-memoir-reflection-lane",
    "guideline-notify-user-bounds",
)


class TestParserHappyPath:
    async def test_c1_listing_serves_the_five_shipped_files(self) -> None:
        raw = await _tools(agent_id="ari", base_dir=REPO_DOCS_GUIDELINES)[
            "guidelines_list"
        ].ainvoke({})
        payload = json.loads(raw)
        entries = payload["guidelines"]
        assert len(entries) == 5, [e["slug"] for e in entries]
        by_slug = {e["slug"]: e for e in entries}
        for slug in CANONICAL_SLUGS:
            entry = by_slug[slug]
            assert entry["slug"] == Path(slug).stem
            assert entry["status"] == "draft", slug
            assert entry["title"] == slug
            assert entry["purpose"], slug
        assert "README" in by_slug and "TEMPLATE" in by_slug
        # Deterministic order (sorted by slug).
        slugs = [e["slug"] for e in entries]
        assert slugs == sorted(slugs)

    async def test_c2_canonical_status_cut_at_the_middle_dot(self) -> None:
        """The canonical status line is ONE physical line (L3) cut at
        the FIRST `` · `` (U+00B7, space-padded) — the wrapped Feature
        tail must never leak into status."""
        raw = await _tools(agent_id="ari", base_dir=REPO_DOCS_GUIDELINES)[
            "guidelines_list"
        ].ainvoke({})
        by_slug = {e["slug"]: e for e in json.loads(raw)["guidelines"]}
        for slug in CANONICAL_SLUGS:
            entry = by_slug[slug]
            assert "·" not in entry["status"], slug
            assert "**Feature:**" not in entry["status"], slug
            assert "**Audience:**" not in entry["status"], slug
            text = (REPO_DOCS_GUIDELINES / f"{slug}.md").read_text(
                encoding="utf-8"
            )
            lines = text.splitlines()
            assert lines[2].startswith("**Status:**"), slug
            # purpose = the first paragraph after the §1 heading
            assert entry["purpose"] in text, slug

    async def test_c5_verbatim_round_trip_all_five(self) -> None:
        tools = _tools(agent_id="ari", base_dir=REPO_DOCS_GUIDELINES)
        read = tools["guideline_read"]
        for path in sorted(REPO_DOCS_GUIDELINES.glob("*.md")):
            expected = path.read_text(encoding="utf-8")
            raw = await read.ainvoke({"slug": path.stem})
            assert raw == expected, f"{path.stem}: not byte-faithful"

    async def test_c6_unknown_slug_refusal_names_all_valid_slugs(self) -> None:
        read = _tools(agent_id="ari", base_dir=REPO_DOCS_GUIDELINES)[
            "guideline_read"
        ]
        raw = await read.ainvoke({"slug": "nope"})
        assert raw.startswith("Error:"), raw
        assert "unknown-slug" in raw, raw
        for path in sorted(REPO_DOCS_GUIDELINES.glob("*.md")):
            assert path.stem in raw, f"{path.stem} missing from the refusal"


class TestParserFailSoft:
    def _write(self, tmp_path: Path, name: str, text: str) -> Path:
        directory = tmp_path / "guidelines"
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text(text, encoding="utf-8")
        return directory

    async def _list(self, base_dir: Path) -> object:
        return await _tools(agent_id="ari", base_dir=base_dir)[
            "guidelines_list"
        ].ainvoke({})

    async def test_c3_no_section_one_falls_back(self, tmp_path: Path) -> None:
        base = self._write(
            tmp_path,
            "guideline-no-section.md",
            "# guideline-no-section\n\n**Status:** draft · **Feature:** x\n\n## 2. States\n\nbody\n",
        )
        raw = await self._list(base)
        entry = json.loads(raw)["guidelines"][0]
        assert entry["slug"] == "guideline-no-section"
        assert entry["status"] == "draft"
        assert entry["purpose"] == "(no purpose section)"

    async def test_c3_no_status_marker_falls_back(self, tmp_path: Path) -> None:
        base = self._write(
            tmp_path,
            "guideline-no-status.md",
            "# guideline-no-status\n\n## 1. Purpose & doctrine\n\nDo the thing.\n",
        )
        raw = await self._list(base)
        entry = json.loads(raw)["guidelines"][0]
        assert entry["status"] == "(no status)"
        assert entry["purpose"] == "Do the thing."

    async def test_c3_empty_file_never_raises(self, tmp_path: Path) -> None:
        base = self._write(tmp_path, "guideline-empty.md", "")
        raw = await self._list(base)
        entry = json.loads(raw)["guidelines"][0]
        assert entry == {
            "slug": "guideline-empty",
            "title": "guideline-empty",
            "status": "(no status)",
            "purpose": "(no purpose section)",
        }

    async def test_c3_h1_mismatching_stem_takes_the_auxiliary_branch(
        self, tmp_path: Path
    ) -> None:
        base = self._write(
            tmp_path,
            "guideline-zzz.md",
            "# guideline-other\n\n**Status:** draft | reviewed\n\n## 1. Purpose & doctrine\n\nBe auxiliary.\n",
        )
        raw = await self._list(base)
        entry = json.loads(raw)["guidelines"][0]
        # slug = the FILENAME stem (the auxiliary fallback), the H1 is
        # the verbatim title, and the pipe is NOT a status separator.
        assert entry["slug"] == "guideline-zzz"
        assert entry["title"] == "guideline-other"
        assert entry["status"] == "draft | reviewed"
        assert entry["purpose"] == "Be auxiliary."

    async def test_c3_auxiliary_wrapped_status_is_rest_of_line_only(
        self, tmp_path: Path
    ) -> None:
        """README-style wrap: the status CONTINUES on the next physical
        line — rest-of-line ONLY (never joined)."""
        base = self._write(
            tmp_path,
            "README.md",
            "# The proposal\n\n**Status:** CANONICAL — homed today;\ncontinues here.\n\n## 1. What\n\nA body paragraph.\n",
        )
        raw = await self._list(base)
        entry = json.loads(raw)["guidelines"][0]
        assert entry["slug"] == "README"
        assert entry["status"] == "CANONICAL — homed today;"
        assert entry["purpose"] == "A body paragraph."

    async def test_c4_absent_and_empty_dirs_refuse_identically(
        self, tmp_path: Path
    ) -> None:
        """The SAME typed refusal from BOTH tools on a missing dir AND
        an empty dir — never an exception, never an empty list."""
        absent = tmp_path / "absent"
        empty = tmp_path / "empty"
        empty.mkdir()
        for base in (absent, empty):
            tools = _tools(agent_id="ari", base_dir=base)
            for name, tool_obj in tools.items():
                raw = await tool_obj.ainvoke(
                    {} if name == "guidelines_list" else {"slug": "x"}
                )
                assert raw.startswith("Error:"), (base, name, raw)
                assert "guidelines-payload-unavailable" in raw, (base, name, raw)
                assert str(base) in raw, (base, name)

    def test_c7_the_surface_exposes_no_write_path(self) -> None:
        """The tool surface is READ-ONLY: the module performs no
        write/unlink/rename/mkdir anywhere, and the AST scan of the
        category module finds EXACTLY the two read tools."""
        source = _module_source()
        for banned in (
            "write_text",
            "write_bytes",
            ".unlink(",
            ".rename(",
            ".replace(",
            ".rmdir(",
            ".mkdir(",
            "open(",
        ):
            assert banned not in source, (
                f"guidelines.py must not carry {banned!r} — read-only surface"
            )
        discovered = discover_source_only_tool_names()
        module_tools = {
            node.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        authored = module_tools & discovered
        assert authored == set(TOOL_NAMES), (
            f"the category module authors exactly the two read tools, got {authored}"
        )


@pytest.mark.parametrize("agent_id", ["ari", "leader", "devops", "sonic-leader"])
async def test_every_granted_agent_can_list_and_read(agent_id: str) -> None:
    """The positive gate — each of the FOUR granted agents lists and
    verbatim-reads the shipped corpus (the one-sentence completion
    criterion)."""
    tools = _tools(agent_id=agent_id, base_dir=REPO_DOCS_GUIDELINES)
    raw = await tools["guidelines_list"].ainvoke({})
    entries = json.loads(raw)["guidelines"]
    assert len(entries) == 5
    raw = await tools["guideline_read"].ainvoke(
        {"slug": "guideline-notify-user-bounds"}
    )
    assert raw == (REPO_DOCS_GUIDELINES / "guideline-notify-user-bounds.md").read_text(
        encoding="utf-8"
    )
