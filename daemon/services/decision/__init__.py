"""Decision module — the ensemble side of the typed overlay.

Design of record: ``.agents/shared/planning/decision-module/``
(``scope-and-doctrine.md`` is the NORMATIVE ANCHOR).

The module is five capability seams:

======================  ==========================================
Seam                    Module
======================  ==========================================
thin client             ``client.py`` (+ ``types.py`` / ``config.py``)
seam registry           ``registry.py``
overlay orchestrator    ``overlay.py``
kill-channel resolver   ``kill.py``
shadow writer           ``shadow.py``
======================  ==========================================

plus two supporting modules — ``metrics.py`` (the metric families) and
``port.py`` (the narrow OUTWARD seam the snapshot side calls).

**THE IMPORT GRAPH IS LAZY ON PURPOSE.** ``port.py`` is the module the
snapshot RANKING module will import; it must not drag the metrics
registry in with it (``controls-and-phasing.md`` §2.5 — the ranking
modules may not import the metrics service, pinned by
``TestMonitoringOnlyPin``). This ``__init__`` therefore resolves its
public names LAZILY (PEP 562), so ``import
daemon.services.decision.port`` never pulls ``metrics``/``overlay`` in.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CallBudget",
    "CircuitBreaker",
    "ClientOutcome",
    "CostAccumulator",
    "DecisionAnswer",
    "DecisionClient",
    "DecisionConfig",
    "DecisionOverlay",
    "DecisionOverlayPort",
    "DecisionQuestion",
    "DecisionRequest",
    "DecisionStage",
    "FailureClass",
    "InMemoryCostAccumulator",
    "KillChannelResolver",
    "log_exception",
    "Monotonicity",
    "OverlayConsultResult",
    "OverlayOutcome",
    "QuestionKind",
    "ReviewRecord",
    "SeamRecord",
    "SeamRegistry",
    "ShadowRow",
    "ShadowWriter",
    "SuppressionReason",
]

#: The public name → module map used by the lazy resolver below.
_EXPORTS: dict[str, str] = {
    "CallBudget": "overlay",
    "CircuitBreaker": "overlay",
    "CostAccumulator": "overlay",
    "DecisionOverlay": "overlay",
    "InMemoryCostAccumulator": "overlay",
    "ClientOutcome": "client",
    "DecisionClient": "client",
    "DecisionConfig": "config",
    "DecisionAnswer": "types",
    "DecisionQuestion": "types",
    "DecisionRequest": "types",
    "DecisionStage": "types",
    "FailureClass": "types",
    "Monotonicity": "types",
    "OverlayOutcome": "types",
    "QuestionKind": "types",
    "SuppressionReason": "types",
    "KillChannelResolver": "kill",
    "log_exception": "logging_safe",
    "OverlayConsultResult": "port",
    "DecisionOverlayPort": "port",
    "ReviewRecord": "registry",
    "SeamRecord": "registry",
    "SeamRegistry": "registry",
    "ShadowRow": "shadow",
    "ShadowWriter": "shadow",
}


def __getattr__(name: str) -> Any:
    """Resolve a public name lazily (PEP 562).

    The lazy resolution is what keeps the narrow outward port
    (``port``) free of the metrics registry: importing
    ``daemon.services.decision.port`` never imports ``metrics`` (and
    therefore never registers the overlay's metric families).
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
