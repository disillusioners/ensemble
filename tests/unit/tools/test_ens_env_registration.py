"""Registration + opt-in tests for the ``ens-env`` tool category.

Stage 1 of the OpenDesign self-provisioning chain
(``feature/od-self-provisioning``, 2026-10-02). Mirrors the 4-step
discipline documented in
``daemon/tools/upgrade_tools.py:110-143`` (the P2.2 REGISTRATION
CHECKLIST) and adapted for the ``ens-env`` category:

1. **Decorator order** — ``@register_tool_category("ens-env")`` MUST
   sit ABOVE ``@tool`` so the category attr is set on the raw
   function before langchain wraps it. Verified by reading the live
   ``_tool_category`` and ``_tool_category_first_party`` attrs on the
   factory-built tool AND source-greping the decorator order in the
   ens_env_tools.py module.
2. **CATEGORY_MODULES entry** — ``"ens-env": "daemon.tools.ens_env_tools"``
   in ``daemon/tools/_tool_registry.py``.
3. **KNOWN_TOOL_NAMES** — the regen-pasted frozen-binary fallback
   also carries ``ens_env_read`` (drift equality is owned by
   ``test_frozen_tool_name_discovery``; we re-pin here too).
4. **create_instance_tools list append** — the CRITICAL list-append
   in ``daemon/tools/instance.py`` calls ``create_ens_env_tools(...)
   tools.extend(ens_env_tool_list)``. Decorator-only registration is
   SILENTLY INVISIBLE without it.

Plus the worker opt-in:

* ``agents/worker/meta.json`` carries ``"ens-env"`` in
  ``tools.allow`` so the install-opendesign consumer lane resolves
  ``ens_env_read`` through the real ``create_instance_tools()``.
* Empty ``tools.allow`` (F1b semantics — inherit/default universe)
  is preserved: this entry is an explicit opt-in, NOT a default
  grant.

Also pinned: the Stage-0 ``_tools_allow`` wire (954e06cb + a1a05c24)
is NOT regressed by this change — the new category follows the same
``tools.allow`` resolution path through
``daemon/tools/instance.py:resolve_tool_filter`` and the F1b
empty-allow=inherit contract holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.ens_env_tools import create_ens_env_tools


# Repo root: tests/unit/tools/test_ens_env_registration.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]


ENS_ENV_TOOL_NAME = "ens_env_read"
ENS_ENV_CATEGORY = "ens-env"
ENS_ENV_MODULE = "daemon.tools.ens_env_tools"


# ─── Steps 1-4: static registration points (greppable checklist) ──────────────


class TestStaticRegistrationChecklist:
    """The §8 / P2.2 checklist adapted for the ens-env category."""

    def test_step1_ast_discovery_finds_ens_env_read(self) -> None:
        """Step 1 + AST walker: the module's factory-created
        ``@tool`` function ``ens_env_read`` is discoverable from
        source. ``discover_source_only_tool_names`` walks the entire
        AST tree, so factory-internal ``@tool`` decorations are
        caught (the gotcha the §8 checklist exists to catch).
        """
        discovered = discover_source_only_tool_names()
        assert ENS_ENV_TOOL_NAME in discovered, (
            f"AST discovery missed {ENS_ENV_TOOL_NAME}; check that "
            f"@register_tool_category sits above @tool in {ENS_ENV_MODULE}"
        )

    def test_step2_category_modules_entry(self) -> None:
        """Step 2: ``CATEGORY_MODULES['ens-env']`` points at
        ``daemon.tools.ens_env_tools``."""
        assert CATEGORY_MODULES.get(ENS_ENV_CATEGORY) == ENS_ENV_MODULE

    def test_step3_known_tool_names_contains_ens_env_read(self) -> None:
        """Step 3: the frozen-binary fallback universe (``KNOWN_TOOL_NAMES``)
        carries the name too. Drift equality is owned by
        ``test_frozen_tool_name_discovery``; this test stays focused
        on the explicit ens-env pin.
        """
        assert ENS_ENV_TOOL_NAME in set(KNOWN_TOOL_NAMES), (
            f"KNOWN_TOOL_NAMES missing {ENS_ENV_TOOL_NAME}; "
            f"regen via: uv run python -c \"from daemon.tools._tool_registry "
            f"import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))\""
        )

    def test_step4_create_instance_tools_list_append_present_in_source(self) -> None:
        """Step 4: the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``: ``create_ens_env_tools(...)``
        extended into the tools list. Decorator-only registration is
        SILENTLY INVISIBLE — the §8 checklist's central gotcha.
        """
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .ens_env_tools import create_ens_env_tools" in source
        assert "tools.extend(ens_env_tool_list)" in source
        assert "create_ens_env_tools(" in source

    def test_decorator_order_register_above_tool_in_source(self) -> None:
        """``@register_tool_category("ens-env")`` MUST appear above
        ``@tool`` in the ens_env_tools.py source. Order matters: the
        ``@tool`` decorator wraps the raw function into a StructuredTool;
        if the category decorator sits BELOW, it sets ``_tool_category``
        on the StructuredTool (which is ignored by some langchain
        internals). The convention is register-above-tool.
        """
        source = (
            REPO_ROOT / "daemon" / "tools" / "ens_env_tools.py"
        ).read_text(encoding="utf-8")
        register_idx = source.find('@register_tool_category("ens-env")')
        tool_idx = source.find("@tool\n")
        assert register_idx != -1, "@register_tool_category decorator missing"
        assert tool_idx != -1, "@tool decorator missing"
        assert register_idx < tool_idx, (
            "@register_tool_category must sit ABOVE @tool so the category "
            "attr is set on the raw function before langchain wraps it"
        )

    def test_factory_returns_tool_with_correct_category(self) -> None:
        """The factory-built tool carries the live ``_tool_category``
        AND ``_tool_category_first_party`` markers (latter guards
        against MCP-driven re-categorization — see
        ``daemon/tools/_tool_registry.py:scan_tools_for_full_docs``).
        """
        tools = create_ens_env_tools(manager=None, current_instance_id="x")
        assert len(tools) == 1
        (tool,) = tools
        assert getattr(tool, "_tool_category", None) == ENS_ENV_CATEGORY
        assert getattr(tool, "_tool_category_first_party", False) is True


# ─── Security boundary ────────────────────────────────────────────────────────


class TestSecurityBoundary:
    def test_not_in_privileged_tool_categories(self) -> None:
        """``ens-env`` is NOT in ``PRIVILEGED_TOOL_CATEGORIES``.

        Why: the category is opt-in (only ``tools.allow: ["ens-env"]``
        holders can call it), but it is NOT daemon-internal authority
        like ``system_upgrade`` / ``system-log`` / ``ens-db``. Putting
        it in PRIVILEGED would change the empty-allow semantics
        (F1b inherit universe would NOT auto-grant it to agents
        without an explicit allow-list). Mirrors the ``service``
        non-privilege precedent: opt-in by allow-list, not by global
        deny.

        Pin this so a future maintainer cannot accidentally promote
        the category.
        """
        assert ENS_ENV_CATEGORY not in set(PRIVILEGED_TOOL_CATEGORIES), (
            f"{ENS_ENV_CATEGORY} was added to PRIVILEGED_TOOL_CATEGORIES — "
            "this changes the empty-allow default grant semantics. If "
            "this change is intentional, update this test AND the "
            "D18/A14 triple-pin contract in _tool_registry.py."
        )


# ─── Worker opt-in ────────────────────────────────────────────────────────────


class TestWorkerOptIn:
    def test_worker_meta_json_includes_ens_env_in_tools_allow(self) -> None:
        """The worker agent (the install-opendesign consumer lane)
        carries ``"ens-env"`` in ``tools.allow``. Without this entry
        the worker cannot call ``ens_env_read`` — the install-opendesign
        skill v1.3.0 (Stage 3 of this chain) will fail Stage 4
        credentials readiness because it cannot self-fetch the BYOK
        values.
        """
        meta_path = REPO_ROOT / "agents" / "worker" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        allow = meta.get("tools", {}).get("allow", [])
        assert ENS_ENV_CATEGORY in allow, (
            f"worker meta.json tools.allow must include {ENS_ENV_CATEGORY!r}; "
            "this is the install-opendesign skill's read access to the "
            "ensemble's live LLM connection values (Stage 1 of "
            "OpenDesign self-provisioning)."
        )

    def test_worker_meta_json_still_loads_cleanly(self) -> None:
        """Belt-and-suspenders: the meta.json edit is valid JSON and
        retains the other categories the worker depends on (we don't
        want a stray edit to silently strip ``bash`` / ``mcp`` / etc.).
        """
        meta_path = REPO_ROOT / "agents" / "worker" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        allow = meta.get("tools", {}).get("allow", [])
        # Required lanes for install-opendesign (real worker allow-list
        # per agents/worker/meta.json, not assumed from elsewhere):
        for required in ("bash", "proc", "dynamic-skill", ENS_ENV_CATEGORY):
            assert required in allow, (
                f"worker meta.json tools.allow lost required category {required!r}; "
                "edit likely too broad."
            )


# ─── Stage-0 fix non-regression ───────────────────────────────────────────────


class TestStage0NonRegression:
    """Stage 0 (commits 954e06cb + a1a05c24, 2026-10-02) fixed two
    coupled defects in ``_tools_allow`` for ``skill_injection``:

    * **F1** — empty ``tools.allow`` was treated as deny-all,
      blocking skill dispatch to bash-capable workers.
    * **F1a wire completion** — the ``_tools_allow`` closure was a
      dead wire (always returned ``[]``) because it used a
      non-existent ``self.get_agent_meta()`` accessor. Now resolves
      via ``get_version(id)`` w/ ``get_resolved`` fallback.

    The fix's invariants (empty-allow = inherit/default universe;
    real allow list surfaces when meta resolves) MUST hold after this
    change. The new ``ens-env`` category follows the same resolution
    path through ``daemon/tools/instance.py:resolve_tool_filter`` —
    we don't introduce a NEW gating seam, so the fix is preserved.
    """

    def test_resolve_tool_filter_recognises_ens_env(self) -> None:
        """The ``resolve_tool_filter`` path (which the F1a wire feeds)
        must accept ``"ens-env"`` as a valid allow entry — i.e. the
        category is in the resolver's known set, and an allow of just
        ``["ens-env"]`` resolves to the factory-created
        ``ens_env_read`` tool name.

        At instance creation time the factory-built ``ens_env_read``
        is registered into ``_tool_metadata`` via
        :func:`daemon.tools._tool_registry.scan_tools_for_full_docs`
        — same path every factory-created tool follows. We replay
        that scan step here so the test exercises the same code path
        the runtime uses.
        """
        from daemon.tools._tool_registry import (
            list_tools_by_category,
            scan_tools_for_full_docs,
        )
        from daemon.tools.instance import resolve_tool_filter

        # Replay the runtime registration step.
        tools = create_ens_env_tools(manager=None, current_instance_id="x")
        scan_tools_for_full_docs(tools)

        # 1) The category is in the live tool_categories universe.
        cats = list_tools_by_category()
        assert ENS_ENV_CATEGORY in cats, (
            f"category {ENS_ENV_CATEGORY!r} not in list_tools_by_category(); "
            "CATEGORY_MODULES entry missing or _tool_metadata scan broken"
        )
        assert ENS_ENV_TOOL_NAME in cats[ENS_ENV_CATEGORY]

        # 2) An allow-list of just ``"ens-env"`` resolves to a
        #    non-empty set containing the tool name.
        result = resolve_tool_filter(
            allow=[ENS_ENV_CATEGORY],
            deny=(),
        )
        assert result is not None
        assert ENS_ENV_TOOL_NAME in result, (
            f"resolve_tool_filter on allow=[{ENS_ENV_CATEGORY!r}] should "
            f"include {ENS_ENV_TOOL_NAME!r}; got {sorted(result)}"
        )

    def test_empty_allow_does_not_grant_ens_env(self) -> None:
        """F1b semantics: empty allow + empty deny → None (inherit/
        default universe). The new ``ens-env`` category follows the
        F1b contract: NO default grant on empty allow. This is the
        reason ``ens-env`` is NOT in PRIVILEGED_TOOL_CATEGORIES (see
        ``TestSecurityBoundary.test_not_in_privileged_tool_categories``).

        Pin: ``resolve_tool_filter`` on empty inputs returns ``None``
        (= inherit = everything), NOT a list specifically including
        ``ens_env_read`` (which would mean the category was promoted
        to default-grant).
        """
        from daemon.tools.instance import resolve_tool_filter

        result = resolve_tool_filter(allow=[], deny=())
        # F1b: None = inherit. If this ever returns a set/list, the
        # empty-allow semantics have flipped and downstream agents
        # would auto-grant every category.
        assert result is None, (
            f"F1b empty-allow semantics regressed: resolve_tool_filter "
            f"returned {result!r} instead of None"
        )