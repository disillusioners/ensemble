"""Unit tests for the ``spawn_instance`` tool docstring (Phase 3).

Pins P and Q cover the docstring discoverability surface for the
``model_tier=\"high\"`` opt-in:

  - Pin P: the runtime ``spawn_instance`` function docstring mentions
    ``model_tier``, the tier literal ``\"high\"``, AND the operator
    env-var ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL``.
  - Pin Q: the docstring explicitly calls out the ASYMMETRY between
    the new ``model_tier=`` (loud ``ValueError``) and the legacy
    ``model=`` (silent fallback). This surfaces the D2 asymmetry to
    the parent LLM at tool-discovery time.

The ``spawn_instance`` runtime function is wrapped via the LangChain
``@tool`` decorator inside ``create_instance_tools``. To read the
docstring, we drive the factory with a stub manager, locate the
``spawn_instance`` tool, and inspect ``tool.description`` /
``tool.func.__doc__``.

``uv run python -m pytest tests/unit/test_spawn_instance_input.py -v``
from the worktree root.
"""

from __future__ import annotations

from daemon.tools.instance import create_instance_tools  # noqa: F821 — imported for tool build
from tests.helpers.send_message_fixtures import (
    make_spawn_manager,
    patch_heavy_helpers,
)


def _build_spawn_instance_tool():
    """Drive ``create_instance_tools`` to find the ``spawn_instance`` tool."""
    manager = make_spawn_manager(allowed_models=["agentic", "coding"])

    patches = patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(
            manager, "parent-instance-id", agent_id="tester"
        )
    finally:
        for p in reversed(patches):
            p.stop()

    for t in tools:
        if getattr(t, "name", None) == "spawn_instance":
            return t
    raise RuntimeError("spawn_instance tool not found")


# ─────────────────────────────────────────────────────────────────────────────
# Pin P — Docstring mentions model_tier / "high" / env-var
# ─────────────────────────────────────────────────────────────────────────────


def test_spawn_instance_docstring_mentions_model_tier_and_high():
    """Pin P (D5 docstring discoverability): the runtime
    ``spawn_instance`` function docstring mentions ``model_tier``
    (param name), ``\"high\"`` (tier literal), AND
    ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`` (operator env-var). The
    parent LLM, reading the tool description, can find all three.
    """
    tool = _build_spawn_instance_tool()
    doc = (tool.func.__doc__ or "") if hasattr(tool, "func") else (tool.description or "")
    # Some LangChain StructuredTool shapes carry the docstring on
    # ``.description`` (the tool's LLM-facing summary) rather than
    # ``.func.__doc__``. Cover both — and concatenate for the search.
    candidates = []
    if hasattr(tool, "description") and tool.description:
        candidates.append(tool.description)
    if hasattr(tool, "func") and tool.func.__doc__:
        candidates.append(tool.func.__doc__)
    doc_blob = "\n".join(candidates)

    assert "model_tier" in doc_blob, (
        f"docstring must mention 'model_tier'; got: {doc_blob[:200]!r}..."
    )
    # Tier literal is rendered in the docstring with EITHER single
    # or double quotes (both are valid LLM-tool description syntax).
    # Accept both forms — pin P only requires the literal "high" to be
    # visible to the parent.
    assert ('"high"' in doc_blob) or ("'high'" in doc_blob), (
        f"docstring must mention tier literal 'high' (single or double quoted); "
        f"got: {doc_blob[:200]!r}..."
    )
    assert "SPAWN_INTELLIGENCE_TIER_HIGH_MODEL" in doc_blob, (
        f"docstring must mention the env-var operator override; got: "
        f"{doc_blob[:200]!r}..."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pin Q — Docstring calls out the loud-vs-silent ASYMMETRY
# ─────────────────────────────────────────────────────────────────────────────


def test_spawn_instance_docstring_mentions_asymmetry():
    """Pin Q (D2 + R3 asymmetry): the docstring explicitly calls out
    the ASYMMETRY between ``model_tier=`` (loud ``ValueError``) and
    ``model=`` (silent fallback). The literal ``ASYMMETRY`` token
    is used (case-sensitive per Phase 3 task 6b) so the parent LLM
    cannot confuse the two semantics.
    """
    tool = _build_spawn_instance_tool()
    candidates = []
    if hasattr(tool, "description") and tool.description:
        candidates.append(tool.description)
    if hasattr(tool, "func") and tool.func.__doc__:
        candidates.append(tool.func.__doc__)
    doc_blob = "\n".join(candidates)

    assert "ASYMMETRY" in doc_blob, (
        f"docstring must call out the ASYMMETRY token; got: {doc_blob[:200]!r}..."
    )
    # And the asymmetry narrative: both terms must appear together.
    assert "ValueError" in doc_blob, (
        f"docstring must mention ValueError for the loud path; got: "
        f"{doc_blob[:200]!r}..."
    )
    assert "silent" in doc_blob.lower(), (
        f"docstring must mention 'silent' for the legacy path; got: "
        f"{doc_blob[:200]!r}..."
    )
