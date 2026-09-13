"""Unit tests for ``append_allowed_models`` — Feature #1 discoverability tail.

Phase 3 of the spawn-time intelligence override plan. Pins M, N, O cover
the three inject-gate branches of the new ``# Spawn Intelligence`` tail
block:

  - Pin M (D5 inject-gate ON, allowed_models populated): the tail
    block appears.
  - Pin N (D5 inject-gate OFF): the tail block does NOT appear (the
    existing fail-open short-circuit at ``instance_lifecycle.py:891``
    bypasses BOTH the existing block AND the new tail).
  - Pin O (Open Question 3 — empty allowed_models): the tail appears
    even when the allowlist is empty (the loud validation will reject
    ``'agentic'`` if it's not in the list — correct behavior, and the
    parent STILL learns the option exists).

``uv run python -m pytest tests/unit/test_append_allowed_models.py -v``
from the worktree root.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.helpers.send_message_fixtures import make_spawn_manager


def _make_agent_meta(
    *,
    inject_allowed_models: bool,
    council_models: list[str] | None = None,
) -> SimpleNamespace:
    """Build a stub agent_meta with the inject-gate flag."""
    return SimpleNamespace(
        inject_allowed_models=inject_allowed_models,
        council_models=council_models,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Imports (deferred until after fixtures so the test file reads top-down)
# ─────────────────────────────────────────────────────────────────────────────


from daemon.services.instance_lifecycle import append_allowed_models  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Pin M — inject-gate ON + populated allowed: tail present
# ─────────────────────────────────────────────────────────────────────────────


def test_append_allowed_models_with_inject_flag_includes_spawn_intelligence_tail():
    """Pin M (D5 inject-gate ON): ``inject_allowed_models=True`` +
    populated ``allowed_models`` → the returned string contains BOTH
    ``"# Spawn Intelligence"`` AND ``"model_tier=\\"high\\""`` markers
    (the tail teaches the governor/council-flow parent about the
    new opt-in).
    """
    manager = make_spawn_manager(allowed_models=["agentic", "coding"])
    agent_meta = _make_agent_meta(inject_allowed_models=True)
    system_prompt = "BASE SYSTEM PROMPT"

    out = append_allowed_models(system_prompt, agent_meta, manager)

    # The new tail block appears.
    assert "# Spawn Intelligence" in out, (
        f"'# Spawn Intelligence' header missing from output: {out!r}"
    )
    assert 'model_tier="high"' in out, (
        f"model_tier='high' opt-in missing from output: {out!r}"
    )
    # The original ``<allowed_models>...</allowed_models>`` fence is
    # preserved (R3.4 — tail lives INSIDE the existing wrapper, NOT a
    # new injection point).
    assert "<allowed_models>" in out
    assert "</allowed_models>" in out
    # And the base system prompt is preserved.
    assert out.startswith(system_prompt)


# ─────────────────────────────────────────────────────────────────────────────
# Pin N — inject-gate OFF: tail does NOT appear (fail-open short-circuit)
# ─────────────────────────────────────────────────────────────────────────────


def test_append_allowed_models_without_inject_flag_excludes_tail():
    """Pin N (D5 inject-gate OFF): ``inject_allowed_models=False``
    (default) → the returned string EQUALS the original ``system_prompt``
    (the fail-open short-circuit at ``instance_lifecycle.py:891-892``
    bypasses BOTH the existing block AND the new tail). No ambient
    context leakage.
    """
    manager = make_spawn_manager(allowed_models=["agentic", "coding"])
    agent_meta = _make_agent_meta(inject_allowed_models=False)
    system_prompt = "BASE SYSTEM PROMPT — UNCHANGED"

    out = append_allowed_models(system_prompt, agent_meta, manager)

    assert out == system_prompt, (
        f"inject-gate OFF must be a no-op (fail-open); got delta: {out!r}"
    )
    assert "# Spawn Intelligence" not in out
    assert "model_tier" not in out


# ─────────────────────────────────────────────────────────────────────────────
# Pin O — Empty allowed_models still includes the tail (OQ3)
# ─────────────────────────────────────────────────────────────────────────────


def test_append_allowed_models_empty_allowed_still_includes_tail():
    """Pin O (Open Question 3): ``allowed_models=[]`` (empty) +
    ``inject_allowed_models=True`` → the tail STILL appears (OQ3
    resolution — tier-availability discoverability should be on the
    table even in the empty-allowed branch; the loud validation
    will reject ``'agentic'`` if it's not in the list). The
    no-restriction text remains the model-list body.
    """
    manager = make_spawn_manager(allowed_models=[])
    agent_meta = _make_agent_meta(inject_allowed_models=True)
    system_prompt = "BASE"

    out = append_allowed_models(system_prompt, agent_meta, manager)

    assert "# Spawn Intelligence" in out, (
        f"tail must appear in empty-allowed branch (OQ3); got: {out!r}"
    )
    assert 'model_tier="high"' in out
    # The empty-allowed branch's "no model restriction" wording is preserved.
    assert "No model restriction" in out
