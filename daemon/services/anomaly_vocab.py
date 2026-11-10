"""The ONE anomaly vocabulary + canonicaliser (M9).

WHY THIS MODULE EXISTS

The anomaly CATEGORY vocabulary and its canonicalisation were implemented
TWICE, with DIVERGENT behaviour:

* ``daemon/services/anomaly_service.py`` carried the canonical
  implementation — ``AnomalyService.normalize_category`` lowercases,
  strips, matches the closed vocabulary and then applies
  ``CATEGORY_SYNONYMS``;
* ``daemon/services/skill_capture_observability.py`` carried a byte
  identical copy of the fallback vocabulary and its OWN
  ``_canonical_category`` — which did NEITHER lowercase NOR apply the
  synonyms.

The same input string therefore canonicalised two ways: ``"PERF"``
folded to ``"performance"`` through the anomaly service and to
``"other"`` through the observability lane. Two copies of a closed
vocabulary also drift: a category added to one is missing from the
other.

THE CONTRACT

One vocabulary, one canonicaliser:

* :data:`ANOMALY_CATEGORIES_FALLBACK` — the FROZEN fallback list, used
  when the metrics lane cannot be imported.
* :data:`CATEGORY_SYNONYMS` — the synonym map for the write-time fold.
* :func:`canonicalize_category` — THE canonicaliser (lowercase → strip →
  exact canonical match → synonym → the reserved ``other``).

Both services delegate to this module.

IMPORT-PURITY (the load-bearing constraint)

This module is **IMPORT-PURE**: it imports NOTHING at module level, and
in particular it never imports ``daemon.services.metrics_query.wiring``
at import time — importing ANY submodule of that PACKAGE executes its
``__init__``, which REGISTERS the phase's metric families in the
process-wide registry (an import-time side effect that diverged the
committed exposition artifact: 18 families at base, 70 with the eager
import).

The live vocabulary therefore lives in the METRICS lane
(``daemon/services/metrics_query/wiring/vocab.py``) and is resolved AT
THE POINT OF USE (inside :func:`resolve_anomaly_categories`), exactly
as the two services did before this module existed. The PEP 562
``__getattr__`` contracts of both services (``ANOMALY_CATEGORIES`` /
``ANOMALY_SEVERITIES`` / ``RESERVED``) keep working unchanged, and the
two import-purity pins stay green.
"""

from __future__ import annotations

#: The frozen fallback category vocabulary (used when the metrics lane
#: cannot be imported). Mirror ``ANOMALY_CATEGORIES`` at
#: ``daemon/services/metrics_query/wiring/vocab.py``.
ANOMALY_CATEGORIES_FALLBACK: tuple[str, ...] = (
    "tool-failure",
    "performance",
    "resource-exhaustion",
    "loop-stall",
    "wedge-suspected",
    "data-corruption",
    "auth-permission",
    "external-dependency",
    "mcp-failure",
    "llm-provider",
    "queue-scheduling",
    "environment-config",
    "other",
)

#: The reserved fold target (used when the metrics lane cannot be
#: imported). Mirror ``RESERVED`` at
#: ``daemon/services/metrics_query/wiring/vocab.py``.
RESERVED_FALLBACK: str = "other"

#: The synonym map for the write-time category fold (R1). The raw
#: input is lowercased and stripped; an EXACT canonical match wins;
#: otherwise a synonym folds; otherwise the reserved ``other``.
CATEGORY_SYNONYMS: dict[str, str] = {
    "tool failure": "tool-failure",
    "tool-failures": "tool-failure",
    "tool-error": "tool-failure",
    "tool_error": "tool-failure",
    "toolerror": "tool-failure",
    "perf": "performance",
    "slow": "performance",
    "latency": "performance",
    "resource-exhausted": "resource-exhaustion",
    "resource exhaustion": "resource-exhaustion",
    "oom": "resource-exhaustion",
    "out-of-memory": "resource-exhaustion",
    "loop-stall": "loop-stall",
    "loop": "loop-stall",
    "stuck-loop": "loop-stall",
    "wedge": "wedge-suspected",
    "wedge-suspect": "wedge-suspected",
    "wedged": "wedge-suspected",
    "corruption": "data-corruption",
    "data-corrupt": "data-corruption",
    "corrupt-data": "data-corruption",
    "auth": "auth-permission",
    "permission": "auth-permission",
    "auth-permissions": "auth-permission",
    "unauthorized": "auth-permission",
    "external": "external-dependency",
    "external-dependencies": "external-dependency",
    "dependency-failure": "external-dependency",
    "mcp": "mcp-failure",
    "mcp-failures": "mcp-failure",
    "mcp-error": "mcp-failure",
    "llm": "llm-provider",
    "llm-failures": "llm-provider",
    "provider-error": "llm-provider",
    "queue": "queue-scheduling",
    "scheduling": "queue-scheduling",
    "queue-scheduling-delay": "queue-scheduling",
    "config": "environment-config",
    "environment": "environment-config",
    "misconfiguration": "environment-config",
    "env-config": "environment-config",
}


def resolve_anomaly_categories() -> tuple[str, ...]:
    """Resolve the closed anomaly categories vocabulary (at the point of use).

    The canonical list lives in the METRICS lane; importing it (or any
    module of the wiring package) registers the phase's metric families
    in the process-wide registry — an import-time side effect. The
    import is therefore made HERE, on the canonicalisation path only.

    Returns:
        The closed vocabulary tuple (the canonical list when the metrics
        lane is importable, the frozen fallback otherwise).
    """
    try:  # the closed vocabulary (imported at the point of use)
        from daemon.services.metrics_query.wiring.vocab import (
            ANOMALY_CATEGORIES,
        )
        return tuple(ANOMALY_CATEGORIES)
    except Exception:  # pragma: no cover — import guard
        return ANOMALY_CATEGORIES_FALLBACK


def resolve_reserved() -> str:
    """Resolve the reserved fold target (at the point of use)."""
    try:  # the reserved target (imported at the point of use)
        from daemon.services.metrics_query.wiring.vocab import (
            RESERVED,
        )
        return str(RESERVED)
    except Exception:  # pragma: no cover — import guard
        return RESERVED_FALLBACK


def canonicalize_category(raw: str) -> tuple[str, str | None]:
    """Fold the raw category into the CLOSED vocabulary (R1).

    Lowercase → strip → exact canonical match → synonym → the reserved
    ``other``. This is THE canonicaliser: the anomaly service and the
    capture-observability lane both delegate to it, so the same input
    string always canonicalises the same way.

    Args:
        raw: The raw category (any case; surrounding space is stripped).

    Returns:
        ``(canonical, fold_notice)`` — the notice is ``None`` when the
        input was already canonical.
    """
    text = (raw or "").strip().lower()
    reserved = resolve_reserved()
    canonical = reserved
    if text:
        categories = resolve_anomaly_categories()
        if text in categories:
            canonical = text
        else:
            canonical = CATEGORY_SYNONYMS.get(text, reserved)
    if canonical == text:
        return canonical, None
    return canonical, f'category "{text}" folded to "{canonical}"'
