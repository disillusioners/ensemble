"""Three-role seam gate (REC §1.2 component 14 — built in ⑤).

The CON §3 three-role seam gate refuses to register a Port unless the
three roles are ALL named:

- **Definition** — the JSON-Schema inputs / outputs + the typed error
  envelope. Owned by us.
- **Provider** — our wrapper (the adapter_id + path). The plugin is a
  resource; it is NEVER a Provider role (CON §3 forbids).
- **Consumer** — named caller(s). Anonymous ("consumer_"*) is refused.

The gate runs at Port-admission time (boot :data). It is structural, not
heuristic; it is the authoritative structural check the schema-CI runner
exercises (and the gate the plugin_tool_factory.py module depends on).

**Provider-without-consumer rejection (CON §3).** A Port that names a
Provider but no Consumer is rejected at the gate regardless of CI status.
This is the explicit CON §3 guarantee that keeps the wrapper surface
useful (a Port no consumer can call is dead weight).

The gate is fail-closed: ANY single role missing ⇒ Port refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Sequence

from daemon.plugin_subsystem.port_registry import (
    ANONYMOUS_CONSUMER,
    Port,
    PortRefusal,
)

__all__ = [
    "SeamGateVerdict",
    "seam_gate_check",
    "is_three_role_complete",
]


@dataclass(frozen=True)
class SeamGateVerdict:
    """The seam-gate verdict for one Port.

    ``ok=True`` ⇔ all three roles named + no anonymous consumer +
    Provider references our wrapper (not the plugin directly).
    """

    ok: bool
    failures: tuple[PortRefusal, ...] = ()
    port_id: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "port_id": self.port_id,
            "failures": [f.as_dict() for f in self.failures],
        }


def _has_role_definition(port: Port) -> bool:
    """Definition role is complete when the schema surfaces are non-empty."""
    return bool(port.inputs_schema) and bool(port.outputs_schema) and bool(port.errors_envelope)


def _has_role_provider(port: Port) -> bool:
    """Provider role is complete when the wrapper is named.

    The plugin is NEVER a Provider (CON §3). The wrapper is our code; the
    plugin's role is the resource/data layer. A non-empty ``adapter_id``
    + a registered path (``B`` / ``C`` / ``A``) is the structural check;
    the wrapper's existence is enforced by the plugin_tool_factory
    integration (slice ⑤) which refuses to bind a Port with an
    unregistered ``adapter_id``.
    """
    return bool(port.adapter_id) and port.provider_path in ("B", "C", "A")


def _has_role_consumer(port: Port) -> bool:
    """Consumer role is complete when at least one NAMED consumer is on the list.

    Empty consumer list is refused; anonymous ``["*"]`` is refused; non-string
    entries are refused. These are validated by :func:`validate_port` upstream
    and re-checked here for defense-in-depth.
    """
    if not port.consumers:
        return False
    if any(c == ANONYMOUS_CONSUMER for c in port.consumers):
        return False
    return all(isinstance(c, str) and bool(c) for c in port.consumers)


def is_three_role_complete(port: Port) -> bool:
    """Boolean three-role check (no refusal details)."""
    return _has_role_definition(port) and _has_role_provider(port) and _has_role_consumer(port)


def seam_gate_check(port: Port) -> SeamGateVerdict:
    """Run the three-role seam gate against one Port.

    Failures are accumulated (not fail-fast) so a single report surfaces
    every missing role. The gate is fail-closed: any failure ⇒ not ok.
    """
    failures: list[PortRefusal] = []

    if not _has_role_definition(port):
        failures.append(
            PortRefusal(
                code="definition_incomplete",
                message=(
                    "Definition role incomplete (CON §3) — inputs_schema / "
                    "outputs_schema / errors_envelope must all be non-empty"
                ),
                port_id=port.port_id,
                location="definition",
            )
        )

    if not _has_role_provider(port):
        failures.append(
            PortRefusal(
                code="provider_incomplete",
                message=(
                    "Provider role incomplete (CON §3) — adapter_id must be "
                    "non-empty and provider.path must be a registered "
                    "path-type row (B / C / A)"
                ),
                port_id=port.port_id,
                location="provider",
            )
        )

    if not _has_role_consumer(port):
        # Distinguish the three consumer failures for the report.
        if not port.consumers:
            failures.append(
                PortRefusal(
                    code="consumer_missing",
                    message=(
                        "Consumer role incomplete (CON §3) — at least one "
                        "named consumer required"
                    ),
                    port_id=port.port_id,
                    location="consumer",
                )
            )
        elif any(c == ANONYMOUS_CONSUMER for c in port.consumers):
            failures.append(
                PortRefusal(
                    code="consumer_anonymous",
                    message=(
                        "Consumer role anonymous refused (CON §3) — "
                        f"'{ANONYMOUS_CONSUMER}' is the refused sentinel"
                    ),
                    port_id=port.port_id,
                    location="consumer",
                )
            )
        else:
            failures.append(
                PortRefusal(
                    code="consumer_invalid",
                    message=(
                        "Consumer role entries must be non-empty strings (CON §3)"
                    ),
                    port_id=port.port_id,
                    location="consumer",
                )
            )

    return SeamGateVerdict(
        ok=not failures,
        failures=tuple(failures),
        port_id=port.port_id,
    )


def seam_gate_check_batch(ports: Sequence[Port]) -> list[SeamGateVerdict]:
    """Run the gate across a sequence of Ports.

    Returns one verdict per input Port (in input order). Caller decides
    whether to refuse the whole batch or proceed on partial success —
    the convention is that ONE failure fails the entire batch (the gate
    is fail-closed).
    """
    return [seam_gate_check(p) for p in ports]


def port_ids_with_consumer(consumer_name: str, ports: Sequence[Port]) -> FrozenSet[str]:
    """Return the set of Port IDs that name ``consumer_name`` on their consumer list.

    A convenience for callers that want to enumerate "which Ports can I call
    from role X?" without taking a registry dependency. Note: a given
    consumer may name more than one Port; this returns the set.
    """
    return frozenset(p.port_id for p in ports if p.has_consumer(consumer_name))