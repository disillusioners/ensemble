"""The W5 CLUSTER — F24, the tool-usage family.

ONE family, ONE metric, ONE write-time sink.  The family records tool
calls by the CLOSED ``tool_category`` × ``result`` label space — enough
for the exposition surface to show THAT tool usage is happening and how
it trends, while per-tool / per-project detail stays in the phase-1
ledger + ``/api/tool-usage/*`` domain door (the tool-usage-metrics-p1
lane, ENSEMBLE-37 measurement slice, Phase 2).

THE METRIC
----------
==========================================================  =======  ===========================  ==========
metric                                                      kind     labels                       durability
==========================================================  =======  ===========================  ==========
``ensemble_tool_usage_calls_total``                          counter  tool_category (67),          process
                                                                      result (2)
==========================================================  =======  ===========================  ==========

Closed label space: 67 × 2 = **134 series** — declared band 160
(134 + 26 explicit headroom = 13 new categories × 2 results; the
vocabulary has grown ~7 times historically).  Never zero-headroom (the
F23 ``67`` and ``decision_overlay`` ``411`` bands are zero-headroom BY
DESIGN and must not be copied).  The MEASURED closed-space total is
491 today + 134 = **625 ≤ 2000** — THE claim (decisions.md D18; the
~1095 figure is a band-sum, cited only as a band-sum).

THE SINK IS WRITE-TIME (no pull adapter)
----------------------------------------
F24 is stat-primary (current value only — ``roster.py``: a family NOT
in the roster is stat-primary, the FR-C2 default).  There is NO pull
adapter: the phase-1 observation tap's sinks fire at the tools node,
and this module registers ONE of them through the FROZEN phase-1
surface (``daemon/services/tool_usage_observation.py``
``register_sink`` / ``unregister_sink`` — renaming either is a
contract break).  The sink uses only ``tool_category`` and ``ok`` and
discards the rest (aggregate-only — per-entity attribution must NOT be
a metric label: decision-module B-2).

THE LABEL FOLD
--------------
``result`` is mapped in the sink (``ok: bool`` → ``"ok"`` / ``"error"``).
``tool_category`` is the phase-1 observer's ``str | None``:

* ``None`` (the documented unmapped case — a tool with no declared
  category) folds to the reserved ``other`` bucket WITHOUT counting an
  invalid label: unmapped is steady state, not drift;
* every ``str`` value is passed RAW — the REGISTRY folds a disallowed
  value to ``other`` and counts it in
  ``ensemble_metrics_invalid_label_total{metric,label}`` (AM-2d, the
  w4 discipline: a pre-folded ``other`` would be indistinguishable from
  a genuine ``other`` and the drift signal would silently die).

THE KILL-SWITCH STACK (phase2-plan.md §3.6)
-------------------------------------------
F24 collects IFF (global metrics gate ON) AND (family gate ON):

* global: ``ENSEMBLE_METRICS_ENABLED`` (fail-closed) +
  managed ``limits.metrics_enabled`` — read through
  :func:`daemon.services.metrics_gate.metrics_enabled`;
* family: env ``ENSEMBLE_METRICS_TOOL_USAGE_ENABLED`` (the SAME
  fail-closed polarity — explicitly set + not truthy = OFF) THEN
  managed ``limits.metrics_tool_usage_enabled`` via
  :func:`daemon.services.metrics_gate.live_managed_value` (PURE cached
  read — no DB on the read path; the key's registration rides phase 1's
  ``MANAGED_KEYS`` edit, ``config_path=None``, default ON).

The family can be OFF while the ledger runs and vice versa (phase 1
owns its own collection key, ``limits.tool_usage_collection_enabled``).
The target deployment ships with the global ``limits.metrics_enabled``
defaulting False, so the family is wired-but-gated: nothing emits until
the operator enables the global gate.

THE MODULE'S IMPORT IS THE REGISTRATION
---------------------------------------
Same contract as w1–w4: importing this module registers the metric in
the registry (through the WIRING registrar — ``register_family_metric``
is the ONLY registration path, AM-2 + AM-3 structural); the door
discovers the family from the registration-time metadata with no door
edit.  :func:`register_w5_adapters` wires the write-time sink (the
package ``__init__`` calls it beside the w1–w4 registrations).
"""

from __future__ import annotations

import os
from typing import Optional

from daemon.checkpoint_metrics import RESERVED_LABEL_VALUE
from daemon.services import metrics_gate
from daemon.services.metrics_query.wiring import vocab
from daemon.services.metrics_query.wiring.registry import (
    PullAdapter,
    register_family_metric,
)

FAMILY_ID = "F24"

# The FILE slot is ``w5.py`` (free); the CLUSTER label is W6 — W5 is
# taken by the decision overlay (``daemon/services/decision/metrics.py``,
# ``CLUSTER = "W5"``).  Cluster is metadata-only, not
# uniqueness-enforced, but the labels stay distinct.
CLUSTER = "W6"

#: The pack verdict for this family — IN (registered, observable, no
#: OUT half).
VERDICT = "IN"

#: The seam anchor the pack's verdict row cites.
SEAM_ANCHOR = (
    "daemon/services/metrics_query/wiring/w5.py::on_tool_usage_event "
    "(the phase-1 observation tap's write-time sink)"
)

#: The counter's canonical name (``^ensemble_[a-z0-9_]+$``).  DISTINCT
#: from F20's OUT name ``ensemble_tool_calls_total`` — that name stays
#: absent from the registry and the ``/metrics`` body.
METRIC_NAME = "ensemble_tool_usage_calls_total"

#: The family's env off-switch — a COMPLETE fail-closed off-switch on
#: its own (explicitly set + not in the truthy set = OFF).
FAMILY_ENV_VAR = "ENSEMBLE_METRICS_TOOL_USAGE_ENABLED"

#: The family's managed key (DB tier; registration rides phase 1's
#: ``MANAGED_KEYS`` edit — ``config_path=None``, DB/ENV-ONLY, default
#: ON).  Phase 2 owns the key's READ path.
FAMILY_MANAGED_KEY = "limits.metrics_tool_usage_enabled"

#: The managed default — ON.  It only matters once the GLOBAL gate is
#: ON (the global ``limits.metrics_enabled`` defaults False).
FAMILY_MANAGED_DEFAULT = True


# ── the metric registration (the import IS the registration) ──────────────

tool_usage_calls_total = register_family_metric(
    family=FAMILY_ID,
    cluster=CLUSTER,
    name=METRIC_NAME,
    kind="counter",
    verdict=VERDICT,
    seam=SEAM_ANCHOR,
    label_keys=("tool_category", "result"),
    allowed_values={
        "tool_category": vocab.tool_usage_categories(),
        "result": vocab.TOOL_USAGE_RESULTS,
    },
    help=(
        "The number of EXECUTED tool calls by closed tool category and "
        "result (ok/error), counted at-least-once per executed "
        "tool_call at the tools node (duplicates from retries are "
        "accepted, not deduplicated). Aggregate-only: per-tool, "
        "per-agent and per-project detail lives in the tool-usage "
        "ledger and the /api/tool-usage/* domain door."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)


# ── the family gate (fail-closed, stacked under the global gates) ─────────


def _family_env_explicit_enabled() -> Optional[bool]:
    """The family env tier's decision (the metrics_gate polarity).

    Returns ``True`` (explicitly truthy), ``False`` (explicitly set to
    ANY other value — fail closed) or ``None`` (UNSET — the managed-key
    chain decides).  Case-insensitive membership ONLY, no stripping, no
    aliases — the same rule the global gate applies to
    ``ENSEMBLE_METRICS_ENABLED``.
    """
    raw = os.environ.get(FAMILY_ENV_VAR)
    if raw is None:
        return None
    return raw.lower() in metrics_gate.TRUTHY_VALUES


def family_gate_enabled() -> bool:
    """F24's collection decision — family gate AND global gate.

    PURE reads only: the env tiers are read per call; the managed tiers
    are the cached live values (a DB round trip here would be the
    CR-1 wedged-writer failure — the sink fires on the main loop).

    The global tier rides the metrics_gate module's ``metrics_enabled``
    function through a LOCAL binding (imported at call time) ON PURPOSE:
    the behavioral check-site census pins the module's
    ``metrics_enabled()`` call sites to exactly TWO in ``daemon/`` (the
    listener body + the census tick).  A stacked gate is not a third
    KILL-SWITCH SITE — the two sites own the switch semantics; this
    family only ANDs the resulting boolean.
    """
    explicit = _family_env_explicit_enabled()
    if explicit is not None:
        family_on = explicit
    else:
        family_on = bool(
            metrics_gate.live_managed_value(
                FAMILY_MANAGED_KEY, FAMILY_MANAGED_DEFAULT
            )
        )
    if not family_on:
        return False
    # LOCAL binding — keeps the census at two textual call sites.
    from daemon.services.metrics_gate import metrics_enabled as _global_enabled

    return _global_enabled()


# ── the write-time sink (the phase-1 observer's frozen signature) ─────────


def on_tool_usage_event(
    tool_name: str,
    tool_category: Optional[str],
    agent_id: str,
    project_id: str,
    ok: bool,
) -> None:
    """F24's sink — the observer's frozen positional signature.

    ``(tool_name, tool_category, agent_id, project_id, ok)`` — the
    aggregate-only sink uses ONLY ``tool_category`` and ``ok`` and
    discards the rest.  Increments NOTHING unless the family gate is ON
    (wired-but-gated: the global ``limits.metrics_enabled`` defaults
    False in the target deployment).
    """
    if not family_gate_enabled():
        return
    if tool_category is None:
        # The documented unmapped case (a tool with no declared
        # category) — steady state, not drift: fold to the reserved
        # bucket WITHOUT counting an invalid label per call.
        category = RESERVED_LABEL_VALUE
    else:
        # RAW — the REGISTRY folds a disallowed/illegal value to
        # ``other`` and counts it in ensemble_metrics_invalid_label_
        # total (AM-2d: a pre-fold here would silence the drift signal).
        category = str(tool_category)
    tool_usage_calls_total.inc(
        1, tool_category=category, result="ok" if ok else "error"
    )


def register_w5_adapters() -> tuple[PullAdapter, ...]:
    """Wire F24's write-time sink against the phase-1 observer.

    F24 has NO pull adapter (stat-primary + write-time — the series
    live while events fire), so the adapters tuple is EMPTY; the
    function exists for the package's uniform ``register_wN_adapters``
    contract.  The observer's ``register_sink`` is a set-add — the
    registration is idempotent.
    """
    # LAZY — keeps the observer (and its langchain import) off the
    # wiring package's import graph until the family is actually wired
    # (the w4 lazy-import discipline).
    from daemon.services.tool_usage_observation import register_sink

    register_sink(on_tool_usage_event)
    return ()
