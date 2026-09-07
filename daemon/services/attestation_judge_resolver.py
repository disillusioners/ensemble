"""Tri-state kill-switch resolver for the LCA inline-LLM completion-report judge.

Phase 6 fastfollow (2026-09-07) of the leader completion attestation
feature. This module is the SINGLE HOME of the LLM-judge kill-switch:

* ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` (default ON — judge
  active; unset / blank / ``true`` / ``1`` / ``yes`` ⇒ enabled; ``0`` /
  ``false`` / ``no`` / ``off`` ⇒ disabled);
* the cached-global Pattern C resolver (restart-read; flip requires
  restart);
* the test-only cache-reset helper.

Pattern C shape mirrors :mod:`daemon.services.attestation_resolver`
(this module's sibling — the main tri-state mode resolver). An
invalid env value (anything other than the four falsy literals
above) falls back to the default ``True`` with NO warn — a typo'd
``yes-pls`` is treated as "enabled" rather than tripping a WARN. This
is the established Pattern C posture for boolean-shape envs in this
codebase: silent default-on is safe (judge is best-effort + fail-safe)
and a mis-flag is operator-recoverable via restart.

Public API
----------

* :func:`is_llm_judge_enabled` — the cached-global resolver;
* :func:`reset_llm_judge_resolver_for_tests` — test-only cache reset.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Env var name (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV = (
    "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED"
)

#: Default ship posture for the LLM judge kill-switch. ON — the judge
#: runs by default on the would-be-deny path. Operators flip ``=0`` /
#: ``=false`` / ``=no`` / ``=off`` to disable for triage; restart is
#: required (Pattern C — no live flip). The default mirrors the
#: house-rule "every flag-gated change ships ON" (Tidier / Tester
#: flag-ON coverage convention).
DEFAULT_LLM_JUDGE_ENABLED: bool = True

#: Falsy literals accepted from the env (lowercase, stripped). The
#: short list mirrors the other kill-switch resolvers in the codebase
#: (``daemon/services/attestation_resolver.py`` blank→default,
#: ``daemon/services/governor_recursion_guard_resolver.py``).
_LLM_JUDGE_FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})


# ─────────────────────────────────────────────────────────────────────────────
# Pattern C cached-global state — restart-read; flip requires restart
# ─────────────────────────────────────────────────────────────────────────────

_CACHED_LLM_JUDGE_ENABLED: bool | None = None


def _parse_llm_judge_enabled(source: dict[str, str]) -> bool:
    """Parse the boolean env, defaulting ON on unset / blank / unknown.

    A blank / unset value returns :data:`DEFAULT_LLM_JUDGE_ENABLED`
    (``True``). A literal in :data:`_LLM_JUDGE_FALSY` returns
    ``False``. Any other non-empty value returns ``True`` (typo'd
    values stay ON — safer than silently disabling; restart-recoverable
    if the operator notices). This is a boolean-shape env, so unlike
    the tri-state ``ENSEMBLE_LEADER_ATTESTATION_MODE`` there is no
    four-value validation here.
    """
    raw = source.get(
        ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, ""
    )
    if raw is None:
        raw = ""
    raw = str(raw).strip().lower()
    if not raw:
        return DEFAULT_LLM_JUDGE_ENABLED
    if raw in _LLM_JUDGE_FALSY:
        return False
    return True


def is_llm_judge_enabled() -> bool:
    """Whether the LLM judge should run on the would-be-deny path.

    Returns the cached value (resolved once at first call; subsequent
    calls return the cached boolean — restart required to flip).
    Pattern C resolver; mirrors the WC-wake / governor-recursion /
    attestation-mode resolvers' identity-preservation contract.
    """
    global _CACHED_LLM_JUDGE_ENABLED
    if _CACHED_LLM_JUDGE_ENABLED is None:
        _CACHED_LLM_JUDGE_ENABLED = _parse_llm_judge_enabled(os.environ)
    return _CACHED_LLM_JUDGE_ENABLED


def reset_llm_judge_resolver_for_tests() -> None:
    """Clear the cached resolver so tests can re-resolve after env mutation.

    Test-only — production code never invokes this. Mutate env vars
    THEN call this helper THEN call :func:`is_llm_judge_enabled` to
    re-resolve under the new env.
    """
    global _CACHED_LLM_JUDGE_ENABLED
    _CACHED_LLM_JUDGE_ENABLED = None