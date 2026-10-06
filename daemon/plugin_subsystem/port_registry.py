"""Plugin Port registry (REC §1.2 component 8 — built in ⑤).

A Port is a **pure data contract** that binds a Provider (our wrapper) to a
Consumer (a named caller) through a Definition (the JSON-Schema port and
typed error envelope — ours, not the plugin's). Every Port is:

- **Process-boundary-clean** (CON §3; load-bearing for later wrapping): the
  inputs/outputs are JSON-serializable data; the wrapper is adapter-not-rewrite.
- **Three-role seam** (CON §3; capability_seam_gate.py enforces this):
  Definition + Provider + Consumer all named or the Port is refused.
- **Stable** (CON §3): ``port_id`` is dotted and stable; ``version`` is
  per-Port (a bump is per-Port, not a manifest-schema bump).

A Port is **not** a tool (the tool factory binds Ports to native tools) and
**not** an agent (the consumer is named — no anonymous ``["*"]``). One Port,
N adapters; the Port is derived from consumer workflow use-cases, never from
a tool registry count.

**Vocabulary zone (CON §7).** This is the tier-2 vocabulary zone alongside
``daemon/plugin_subsystem/`` — the only package in the daemon that may
reference Port vocabulary (``port_id``, ``capability_tags``, …). Tier-1
modules stay structurally blind.

**Negative tests (CON §3).** :func:`validate_port_serializability` refuses
any non-JSON-serializable type in a Port schema (raw object / callable /
datetime-without-format / binary-without-contentEncoding). The
``schema_ci.run_ci`` aggregate wires these into the manifest CI run as a
sibling step; a failing Port fails the build, the adapter is not built.

**No runtime loading (CON §7 sentinel).** The registry is DATA + explicit
validation; no ``importlib``, no entry-point scan. All providers live
behind the typed surface; the registry never loads plugin code.

**Slice scope (REC §4.3 row ⑤).** This module ships the registry surface,
the typed dataclass, the validation gate, and the lookup API. The first
real Ports (``od.generate`` / ``od.compose_brief`` / ``od.save`` /
``od.lint``) are declared in :mod:`daemon.plugin_subsystem.opendesign.ports`
and registered by :func:`build_default_port_registry` (the slice-⑤
opendesign-instance wiring). Plugin #2+ re-uses the same registry
surface.

**Refusal codes** (``PortRefusal.code``; snake_case; CON §5 codes reused
where they map):

- ``port_id_invalid`` — ``port_id`` not in dotted-kebab form (a leading
  alpha segment, dot-separated; e.g. ``od.generate``).
- ``version_invalid`` — ``version`` missing, non-int, or ≤0.
- ``definition_missing`` — ``definition`` block absent.
- ``outputs_schema_missing`` — ``outputs_schema`` block absent.
- ``inputs_schema_missing`` — ``inputs_schema`` block absent.
- ``errors_envelope_missing`` — ``definition.errors.envelope`` block
  absent or not carrying the CON-mandated
  ``{ok: false, code, message, details?}`` keys.
- ``capability_tags_invalid`` — ``capability_tags`` present but not a
  list of non-empty strings.
- ``provider_missing`` — ``provider`` block absent (Provider role).
- ``adapter_id_invalid`` — ``provider.adapter_id`` missing or empty.
- ``provider_path_invalid`` — ``provider.path`` missing, empty, or not
  one of the registered path-type rows.
- ``consumer_missing`` — ``consumer`` block absent or empty.
- ``consumer_anonymous`` — ``consumer`` is ``["*"]`` (anonymous refused).
- ``non_serializable_input`` — schema declares a non-JSON-serializable
  type (the gate detects raw object/callable/datetime-without-format/
  binary-without-contentEncoding at validate time).
- ``non_serializable_output`` — same for the outputs schema.
- ``duplicate_port_id`` — the same ``port_id`` is registered twice.
- ``duplicate_consumer_port`` — a consumer name appears on more than
  one Port's ``consumer`` list (no port-shadowing; consumers must
  pick).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, FrozenSet, List, Mapping, Optional, Sequence

__all__ = [
    "Port",
    "PortRefusal",
    "PortRegistry",
    "build_default_port_registry",
    "validate_port_serializability",
    "PORT_ID_PATTERN",
    "ANONYMOUS_CONSUMER",
]


# Port IDs are dotted-kebab: ``od.generate``, ``opendesign.compose_brief``.
# A leading alpha segment, dot-separated, lowercase + hyphens + underscores.
# The regex is FROZEN per CON §3 — changing it is a breaking event for
# every plugin. Underscores are accepted because the canonical opendesign
# Port IDs (``od.compose_brief``) use them; the dotted-kebab "primary"
# form is the kebab-case shape, with underscores as a tolerated cousin.
PORT_ID_PATTERN: str = r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$"

# Anonymous consumer is refused (CON §3). Sentinel constant for clarity.
ANONYMOUS_CONSUMER: str = "*"


@dataclass(frozen=True)
class Port:
    """A validated, typed Port (CON §3).

    Built by :func:`build_default_port_registry` (or a per-plugin analogue)
    after the structural + semantic gates pass. Consumers of this class
    (the plugin tool factory, the schema-CI runner) treat it as the
    authoritative data contract for one Port.
    """

    port_id: str
    version: int

    # Definition role (CON §3) — OWNED BY US
    inputs_schema: Mapping[str, Any]
    outputs_schema: Mapping[str, Any]
    errors_envelope: Mapping[str, Any]
    capability_tags: FrozenSet[str] = field(default_factory=frozenset)

    # Provider role — OUR wrapper (the plugin is a resource, never a role)
    adapter_id: str = ""
    provider_path: str = ""  # "B" | "C" | "A"

    # Consumer role — NAMED ONLY (no anonymous consumers)
    consumers: Sequence[str] = field(default_factory=tuple)

    # Optional provenance: where this Port is declared.
    declared_in: Optional[str] = None  # e.g. "plugins/opendesign/MANIFEST.yaml"

    # Error-envelope key expectations — the canonical three keys per CON §3.
    # The Port must include all three keys (ok:false, code, message); details is
    # OPTIONAL. The gate enforces this at validate time; here we record the
    # validated envelope shape for downstream consumers (the adapter tool
    # factory, the schema-CI runner).
    def expected_error_envelope_keys(self) -> FrozenSet[str]:
        """The required keys in every error envelope (CON §3).

        ``ok`` is fixed to ``false`` at the error site (the gate verifies the
        schema declares the ``ok`` key as a constant ``false``).
        """
        return frozenset({"ok", "code", "message"})

    def has_consumer(self, consumer_name: str) -> bool:
        """True iff ``consumer_name`` is named on this Port."""
        return consumer_name in self.consumers


# Refusal dataclass mirrors the manifest reader's refusal shape so the
# schema-CI aggregate and CLI exit-code logic are uniform across manifest
# + port validation.


@dataclass(frozen=True)
class PortRefusal:
    """One Port validation refusal.

    Carries a stable ``code`` (CON §5 reuse where it maps; port-specific
    codes are prepended) so the schema-CI report is uniform.
    """

    code: str
    message: str
    port_id: str = ""
    location: str = ""
    declared_in: str = ""

    def as_dict(self) -> dict[str, str]:
        d: dict[str, str] = {"code": self.code, "message": self.message}
        if self.port_id:
            d["port_id"] = self.port_id
        if self.location:
            d["location"] = self.location
        if self.declared_in:
            d["declared_in"] = self.declared_in
        return d


# ---------------------------------------------------------------------------
# Negative tests for JSON-serializability (CON §3)
# ---------------------------------------------------------------------------


def validate_port_serializability(
    schema: Mapping[str, Any], *, location: str = ""
) -> Optional[PortRefusal]:
    """Walk a JSON-Schema fragment and refuse any non-JSON-serializable shape.

    The CON §3 gate: a Port schema that declares a non-JSON-serializable type
    is refused at validation time; the adapter is NOT built. The detection
    surface mirrors the planner prose:

    - **raw object / ``callable``** — refused (no in-process types on the
      wire; ``callable`` is rejected by ``type: "object"`` schema keyword,
      but a schema declaring ``{}`` / ``{"type": "object"}`` without
      ``properties`` allows arbitrary — we refuse bare ``{}`` because the
      wrapper would have no contract surface).
    - **datetime-without-format** — refused (``"format": "date-time"`` is
      allowed because it IS JSON-serializable; bare ``{"type": "string",
      "format": "..."}`` is allowed iff the format is a known JSON Schema
      string format. A custom non-standard format that doesn't exist in
      the JSON-Schema spec is refused).
    - **binary-without-contentEncoding** — refused (``{"type": "string",
      "contentEncoding": "base64"}`` is allowed; bare ``{"type": "string",
      "contentMediaType": "..."}`` without contentEncoding is refused).

    Returns ``None`` when the schema is clean, a :class:`PortRefusal` on
    the first violation found (fail-fast; the gate refuses by construction).
    """
    if not isinstance(schema, Mapping):
        return PortRefusal(
            code="non_serializable_input",
            message="schema must be a JSON object",
            location=location,
        )

    schema_type = schema.get("type")
    if schema_type is None:
        # Bare schema with no type — schema is too loose to enforce
        # any wrapper contract. Per CON §3 negative-test rule, a Port
        # schema must declare types (else a wrapper has nothing to bind).
        return PortRefusal(
            code="non_serializable_input",
            message="schema must declare 'type' (CON §3 wrapper contract)",
            location=location,
        )

    # JSON Schema 2020-12 nullable syntax: ``"type": ["string", "null"]`` —
    # accept when every element is a primitive type we know is JSON-
    # serializable. The wrapper treats the null arm as Optional[T].
    if isinstance(schema_type, list):
        if not schema_type:
            return PortRefusal(
                code="non_serializable_input",
                message="type list must be non-empty (CON §3 wrapper contract)",
                location=location,
            )
        for sub_type in schema_type:
            if sub_type in ("number", "integer", "boolean", "null", "string"):
                continue
            return PortRefusal(
                code="non_serializable_input",
                message=(
                    f"unsupported type list element '{sub_type}' (CON §3 "
                    f"non-serializable refusal; use a primitive JSON Schema type)"
                ),
                location=location,
            )
        # Run the deeper checks for each string-typed element.
        # For simplicity (and because the wrapper contract gates only on the
        # primitive type), we walk into the schema once per element. The
        # string element's contentMediaType / format guards fire below.
        if "string" in schema_type:
            if "contentMediaType" in schema and "contentEncoding" not in schema:
                return PortRefusal(
                    code="non_serializable_input",
                    message=(
                        "string schema with 'contentMediaType' must also "
                        "declare 'contentEncoding' (CON §3 binary-without-"
                        "contentEncoding refusal)"
                    ),
                    location=location,
                )
            if "format" in schema:
                fmt = schema["format"]
                if fmt not in _JSON_SCHEMA_STRING_FORMATS:
                    return PortRefusal(
                        code="non_serializable_input",
                        message=(
                            f"unknown string format '{fmt}' — must be one of "
                            f"{sorted(_JSON_SCHEMA_STRING_FORMATS)} or omit "
                            f"'format' (CON §3 datetime-without-format refusal)"
                        ),
                        location=location,
                    )
        return None

    if schema_type == "object":
        # Bare ``{}`` / ``{"type": "object"}`` without properties —
        # refuses because the wrapper has no contract surface.
        if "properties" not in schema and "additionalProperties" not in schema:
            # ``additionalProperties: false`` is acceptable — that itself
            # is a contract. But absent both, refuse.
            return PortRefusal(
                code="non_serializable_input",
                message=(
                    "object schema must declare 'properties' (and is "
                    "recommended to set additionalProperties: false) — "
                    "CON §3 wrapper contract"
                ),
                location=location,
            )
        # Walk into properties.
        for prop_name, prop_schema in schema.get("properties", {}).items():
            child_loc = f"{location}.properties.{prop_name}" if location else f"properties.{prop_name}"
            refusal = validate_port_serializability(prop_schema, location=child_loc)
            if refusal is not None:
                return refusal
        return None

    if schema_type == "array":
        items = schema.get("items")
        if items is None:
            return PortRefusal(
                code="non_serializable_input",
                message="array schema must declare 'items' (CON §3 wrapper contract)",
                location=location,
            )
        child_loc = f"{location}.items" if location else "items"
        return validate_port_serializability(items, location=child_loc)

    if schema_type == "string":
        # contentMediaType without contentEncoding is refused (binary
        # blobs without an encoding spec are non-serializable on the wire).
        if "contentMediaType" in schema and "contentEncoding" not in schema:
            return PortRefusal(
                code="non_serializable_input",
                message=(
                    "string schema with 'contentMediaType' must also "
                    "declare 'contentEncoding' (CON §3 binary-without-"
                    "contentEncoding refusal)"
                ),
                location=location,
            )
        # A non-standard format (anything not in the JSON Schema known
        # formats + content* spec) is refused because the wrapper would
        # not know how to transport the value on the wire.
        if "format" in schema:
            fmt = schema["format"]
            if fmt not in _JSON_SCHEMA_STRING_FORMATS:
                return PortRefusal(
                    code="non_serializable_input",
                    message=(
                        f"unknown string format '{fmt}' — must be one of "
                        f"{sorted(_JSON_SCHEMA_STRING_FORMATS)} or omit "
                        f"'format' (CON §3 datetime-without-format refusal)"
                    ),
                    location=location,
                )
        return None

    if schema_type in ("number", "integer", "boolean", "null"):
        return None

    # Refuse exotic types outright (callable, raw bytes, datetime).
    return PortRefusal(
        code="non_serializable_input",
        message=(
            f"unsupported schema type '{schema_type}' (CON §3 "
            f"non-serializable refusal; use a JSON Schema primitive type)"
        ),
        location=location,
    )


# JSON Schema's known string formats (subset we accept per CON §3).
# date-time and date are JSON-serializable; everything else in this set is too.
# A schema declaring a format OUTSIDE this set is refused.
_JSON_SCHEMA_STRING_FORMATS: FrozenSet[str] = frozenset({
    "date-time",
    "date",
    "time",
    "email",
    "uuid",
    "uri",
    "ipv4",
    "ipv6",
    "hostname",
    "regex",
})


# ---------------------------------------------------------------------------
# Port registry — DATA only (no runtime loading)
# ---------------------------------------------------------------------------


class PortRegistry:
    """Boot-time Port table (DATA + explicit validation; CON §7 sentinel).

    Lookup-by-port-id and lookup-by-consumer are pure data reads. The
    registry NEVER imports plugin source; provider internals are bound by
    the plugin tool factory at consumption time, not at registration.
    """

    def __init__(self, ports: Sequence[Port]) -> None:
        self._ports_by_id: dict[str, Port] = {}
        self._ports_by_consumer: dict[str, list[Port]] = {}
        self._ports_by_tag: dict[str, list[Port]] = {}

        # Build the lookup maps. ``PortRegistry.__init__`` does not re-validate
        # — callers pass already-validated Ports (the gate lives in
        # :func:`validate_port`). The ``DuplicatePort`` / ``DuplicateConsumer``
        # checks below are duplicate-id checks at registration time (cheap).
        seen_ids: set[str] = set()
        seen_consumer_port: set[tuple[str, str]] = set()
        for port in ports:
            if port.port_id in seen_ids:
                raise ValueError(
                    f"duplicate port_id {port.port_id!r} (CON §3 duplicate_port_id)"
                )
            seen_ids.add(port.port_id)
            self._ports_by_id[port.port_id] = port

            for consumer in port.consumers:
                key = (consumer, port.port_id)
                if key in seen_consumer_port:
                    continue
                seen_consumer_port.add(key)
                self._ports_by_consumer.setdefault(consumer, []).append(port)

            for tag in port.capability_tags:
                self._ports_by_tag.setdefault(tag, []).append(port)

    # --- lookup -------------------------------------------------------------

    def get(self, port_id: str) -> Optional[Port]:
        """Lookup-by-port-id. Returns ``None`` when not registered."""
        return self._ports_by_id.get(port_id)

    def by_consumer(self, consumer_name: str) -> Sequence[Port]:
        """All Ports that name ``consumer_name`` on their consumer list."""
        return tuple(self._ports_by_consumer.get(consumer_name, ()))

    def by_capability_tag(self, tag: str) -> Sequence[Port]:
        """All Ports declaring ``tag`` in their capability_tags set.

        CON §3: ``capability_tags`` is **catalog only**, not routing.
        Consumers use this for discovery (which Ports match my tag?), not
        for "send me to the right Port". Routing is always by
        ``port_id`` (named consumer + explicit Port bind).
        """
        return tuple(self._ports_by_tag.get(tag, []))

    def all(self) -> Sequence[Port]:
        """Snapshot of all registered Ports (immutable tuple)."""
        return tuple(self._ports_by_id.values())

    def __len__(self) -> int:
        return len(self._ports_by_id)

    def __contains__(self, port_id: object) -> bool:
        return isinstance(port_id, str) and port_id in self._ports_by_id


# ---------------------------------------------------------------------------
# Validation gate (single entry: validate_port -> Port | PortRefusal)
# ---------------------------------------------------------------------------


def validate_port(raw: Mapping[str, Any], *, declared_in: str = "") -> tuple[Optional[Port], Optional[PortRefusal]]:
    """Validate one raw port-dict against the CON §3 contract surface.

    Returns ``(port, None)`` on success, ``(None, refusal)`` on failure.
    Failures are FAIL-FAST (first refusal wins) so the gate reports a
    single root cause per Port; consumers iterate when they need to
    surface multiple issues.

    The validator re-uses :func:`validate_port_serializability` for the
    JSON-serializability negative tests.
    """
    # 1. ``port_id`` — dotted-kebab, leading alpha.
    port_id = raw.get("port_id")
    if not isinstance(port_id, str) or not port_id:
        return None, PortRefusal(
            code="port_id_invalid",
            message="'port_id' is required and must be a non-empty string",
            declared_in=declared_in,
        )

    import re

    if not re.match(PORT_ID_PATTERN, port_id):
        return None, PortRefusal(
            code="port_id_invalid",
            message=(
                f"port_id {port_id!r} does not match the CON §3 dotted-kebab "
                f"pattern (e.g. 'od.generate', 'opendesign.compose_brief')"
            ),
            port_id=port_id,
            declared_in=declared_in,
        )

    # 2. ``version`` — positive int.
    version = raw.get("version")
    if not isinstance(version, int) or version <= 0:
        return None, PortRefusal(
            code="version_invalid",
            message=f"'version' must be a positive int (got {version!r})",
            port_id=port_id,
            declared_in=declared_in,
        )

    # 3. ``definition`` block (Definition role — ours).
    definition = raw.get("definition")
    if not isinstance(definition, Mapping):
        return None, PortRefusal(
            code="definition_missing",
            message="'definition' block is required (CON §3 Definition role)",
            port_id=port_id,
            declared_in=declared_in,
        )

    inputs_schema = definition.get("inputs_schema")
    if not isinstance(inputs_schema, Mapping):
        return None, PortRefusal(
            code="inputs_schema_missing",
            message="'definition.inputs_schema' is required (CON §3 JSON-Schema surface)",
            port_id=port_id,
            declared_in=declared_in,
        )
    refusal = validate_port_serializability(inputs_schema, location="definition.inputs_schema")
    if refusal is not None:
        return None, PortRefusal(
            code=refusal.code,
            message=f"inputs_schema: {refusal.message}",
            port_id=port_id,
            location=refusal.location or "definition.inputs_schema",
        )

    outputs_schema = definition.get("outputs_schema")
    if not isinstance(outputs_schema, Mapping):
        return None, PortRefusal(
            code="outputs_schema_missing",
            message="'definition.outputs_schema' is required (CON §3 JSON-Schema surface)",
            port_id=port_id,
            declared_in=declared_in,
        )
    refusal = validate_port_serializability(outputs_schema, location="definition.outputs_schema")
    if refusal is not None:
        return None, PortRefusal(
            code=refusal.code,
            message=f"outputs_schema: {refusal.message}",
            port_id=port_id,
            location=refusal.location or "definition.outputs_schema",
        )

    # 4. ``errors.envelope`` — typed envelope, MANDATORY (CON §3).
    errors = definition.get("errors")
    if not isinstance(errors, Mapping):
        return None, PortRefusal(
            code="errors_envelope_missing",
            message="'definition.errors' block is required (CON §3 typed error envelope)",
            port_id=port_id,
            declared_in=declared_in,
        )
    envelope = errors.get("envelope")
    if not isinstance(envelope, Mapping):
        return None, PortRefusal(
            code="errors_envelope_missing",
            message=(
                "'definition.errors.envelope' is required and must carry the "
                "CON §3 keys: ok (constant false), code, message (details optional)"
            ),
            port_id=port_id,
            declared_in=declared_in,
        )
    for required_key in ("ok", "code", "message"):
        if required_key not in envelope:
            return None, PortRefusal(
                code="errors_envelope_missing",
                message=(
                    f"definition.errors.envelope missing required key "
                    f"{required_key!r} (CON §3 mandates ok + code + message)"
                ),
                port_id=port_id,
                location="definition.errors.envelope",
            )
    # The ``ok`` key MUST be a constant false (CON §3: error envelope is
    # {ok: false, code, message, details?}). The only valid shape is
    # ``{"const": false}``; any other shape (boolean true, enum, type-only)
    # is refused because the error envelope must always report ok=false.
    ok_value = envelope.get("ok")
    if not (isinstance(ok_value, Mapping) and ok_value.get("const") is False):
        return None, PortRefusal(
            code="errors_envelope_missing",
            message=(
                "definition.errors.envelope.ok must be {\"const\": false} "
                "(CON §3 mandates the error envelope always reports ok=false)"
            ),
            port_id=port_id,
            location="definition.errors.envelope.ok",
        )

    # 5. ``capability_tags`` — optional catalog. When present, must be a
    # list of non-empty strings.
    raw_tags = definition.get("capability_tags", [])
    tags: list[str]
    if isinstance(raw_tags, list):
        if not all(isinstance(t, str) and t for t in raw_tags):
            return None, PortRefusal(
                code="capability_tags_invalid",
                message="'definition.capability_tags' must be a list of non-empty strings",
                port_id=port_id,
                location="definition.capability_tags",
            )
        tags = list(raw_tags)
    elif isinstance(raw_tags, tuple):
        if not all(isinstance(t, str) and t for t in raw_tags):
            return None, PortRefusal(
                code="capability_tags_invalid",
                message="'definition.capability_tags' must be a list of non-empty strings",
                port_id=port_id,
                location="definition.capability_tags",
            )
        tags = list(raw_tags)
    else:
        return None, PortRefusal(
            code="capability_tags_invalid",
            message="'definition.capability_tags' must be a list of non-empty strings",
            port_id=port_id,
            location="definition.capability_tags",
        )

    # 6. ``provider`` block (Provider role — ours, the wrapper).
    provider = raw.get("provider")
    if not isinstance(provider, Mapping):
        return None, PortRefusal(
            code="provider_missing",
            message="'provider' block is required (CON §3 Provider role = our wrapper)",
            port_id=port_id,
            declared_in=declared_in,
        )
    adapter_id = provider.get("adapter_id")
    if not isinstance(adapter_id, str) or not adapter_id:
        return None, PortRefusal(
            code="adapter_id_invalid",
            message="'provider.adapter_id' is required and must be a non-empty string",
            port_id=port_id,
            location="provider.adapter_id",
        )
    provider_path = provider.get("path")
    if provider_path not in ("B", "C", "A"):
        return None, PortRefusal(
            code="provider_path_invalid",
            message=(
                f"'provider.path' must be 'B', 'C', or 'A' "
                f"(registry path-type rows); got {provider_path!r}"
            ),
            port_id=port_id,
            location="provider.path",
        )

    # 7. ``consumer`` block (Consumer role — NAMED ONLY).
    consumer = raw.get("consumer")
    if not isinstance(consumer, list) or not consumer:
        return None, PortRefusal(
            code="consumer_missing",
            message="'consumer' block is required and must be a non-empty list (CON §3)",
            port_id=port_id,
            location="consumer",
        )
    if any(c == ANONYMOUS_CONSUMER for c in consumer):
        return None, PortRefusal(
            code="consumer_anonymous",
            message=(
                f"'consumer' lists anonymous consumers (['*'] refused, "
                f"CON §3 — named only)"
            ),
            port_id=port_id,
            location="consumer",
        )
    consumers = tuple(consumer)
    if not all(isinstance(c, str) and c for c in consumers):
        return None, PortRefusal(
            code="consumer_missing",
            message="'consumer' list must contain non-empty string entries (CON §3)",
            port_id=port_id,
            location="consumer",
        )

    port = Port(
        port_id=port_id,
        version=version,
        inputs_schema=dict(inputs_schema),
        outputs_schema=dict(outputs_schema),
        errors_envelope=dict(envelope),
        capability_tags=frozenset(tags),
        adapter_id=adapter_id,
        provider_path=provider_path,
        consumers=consumers,
        declared_in=declared_in or None,
    )
    return port, None


def validate_ports(raw_ports: Sequence[Mapping[str, Any]], *, declared_in: str = "") -> tuple[List[Port], List[PortRefusal]]:
    """Validate a sequence of raw Port dicts.

    Returns ``(ports, refusals)``. Empty-refusals list is the success path;
    a single refusal in the list is a hard failure (the entire batch is
    rejected — adapters are not built from a partial batch).
    """
    ports: list[Port] = []
    refusals: list[PortRefusal] = []
    for raw in raw_ports:
        port, refusal = validate_port(raw, declared_in=declared_in)
        if port is not None:
            ports.append(port)
        elif refusal is not None:
            refusals.append(refusal)
    return ports, refusals


# ---------------------------------------------------------------------------
# Default Port registry builder (slice ⑤: opendesign-instance)
# ---------------------------------------------------------------------------


def build_default_port_registry() -> PortRegistry:
    """Build the boot-time Port registry by aggregating every registered
    plugin's declared Ports.

    For slice ⑤ the only plugin with declared Ports is opendesign; the
    builder delegates to that plugin's port-declaration module (the
    single source of truth for plugin Port declarations — declarative
    YAML in the manifest, validated at boot).

    The builder raises on duplicate-port-id / duplicate-consumer-port
    (the registry itself is fail-closed at construction time; CI failures
    surface at boot).
    """
    # Import here (not at module top) so the port_registry module can be
    # imported without the opendesign-instance slice being on the path
    # (e.g. for unit tests that exercise only the registry surface).
    from daemon.plugin_subsystem.opendesign.ports import declared_opendesign_ports  # noqa: E402 - circular-import avoidance

    ports, refusals = validate_ports(declared_opendesign_ports(), declared_in="plugins/opendesign/MANIFEST.yaml")
    if refusals:
        # Surface the first refusal as the boot error; the full list is
        # logged by the calling site (typically the boot scan in
        # ``daemon/manager.py`` integration at slice ⑥).
        first = refusals[0]
        raise ValueError(
            f"Port registry construction refused ({first.code}): "
            f"{first.message} (port_id={first.port_id!r}, "
            f"location={first.location!r})"
        )
    return PortRegistry(ports)