"""Plugin tool factory (REC §1.2 component 12 — built in ⑤).

Binds registered :class:`~daemon.plugin_subsystem.port_registry.Port`
declarations to native LangChain tools that the agent-tool lane can
invoke. Each Port → one tool: the factory reads the Port's inputs/
outputs schemas + the three-role seam (Definition+Provider+Consumer)
and emits a ``@register_tool``-decorated tool function bound to the
adapter class.

**Tier-1 wiring.** The factory is invoked at instance-tool assembly
time (``daemon/tools/instance.py:create_instance_tools`` — wired at
slice ⑤, flagged in the report). The factory consumes the global Port
registry (:func:`build_default_port_registry`) plus the per-plugin
adapter classes (``daemon/plugin_subsystem/opendesign/{generate,
compose_brief, save, lint}.py``); each Port's ``adapter_id`` field
selects the adapter via :func:`ADAPTER_REGISTRY`.

**No runtime loading (CON §7 sentinel).** The factory imports the
adapter modules explicitly (via the in-process ``ADAPTER_REGISTRY``
table). No ``importlib`` / entry-point scan. Plugin #2+ registers a
new ``adapter_id`` → module binding by appending to ``ADAPTER_REGISTRY``
at the bottom of this file (a sibling file lives at
``daemon/plugin_subsystem/opendesign/<capability>.py`` for the
opendesign adapters, all eagerly imported so the binding is static).

**Three-role seam (CON §3).** The factory refuses to build a tool for
any Port that fails the seam gate
(:func:`~daemon.plugin_subsystem.capability_seam_gate.seam_gate_check`).
The refusal surfaces in :func:`build_tools_for_port` as a raised
exception (the boot scan in ``daemon/manager.py`` logs the refusal
and continues; the tool simply does not exist for that Port — the
agent sees a "tool not bound" probe result, exactly as if the upstream
MCP server were absent).

**Adapter dispatch.** :data:`ADAPTER_REGISTRY` maps ``adapter_id`` →
``(module_path, callable_name)``. The factory imports the module via
``importlib.import_module`` (the sanctioned use — explicit module
imports, not entry-point scanning) and grabs the callable by name.
This is the single sanctioned runtime-loading surface in the plugin
subsystem (CON §7 — the sentinel forbids ``importlib`` /
``pkg_resources`` / entry-point scans; the adapter-id → module binding
table is the structured override).
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from daemon.plugin_subsystem.capability_seam_gate import (
    is_three_role_complete,
    seam_gate_check,
)
from daemon.plugin_subsystem.port_registry import (
    Port,
    PortRegistry,
    build_default_port_registry,
)

__all__ = [
    "ADAPTER_REGISTRY",
    "build_tools_for_port",
    "build_plugin_tools",
    "register_adapter",
]


# ---------------------------------------------------------------------------
# Adapter registry (data table — no entry-point scan, no importlib on disk)
# ---------------------------------------------------------------------------
#
# Each entry maps an adapter_id (the ``Port.provider.adapter_id`` field)
# to the (module, callable) tuple that implements the adapter. The
# adapter module exposes a top-level callable that accepts the Port's
# inputs_schema-shaped dict and returns the outputs_schema-shaped dict.
#
# Adding a new plugin: append a new entry here. The adapter_id MUST
# match the Port's ``provider.adapter_id`` exactly. The factory refuses
# to build a tool when the adapter_id is not registered (the caller
# catches the refusal at boot time).
#
# Plugin #1 (opendesign) ships with four adapters; the slice-⑤ opendesign
# B-element implements each as a class-level static ``execute_dict`` /
# ``compose_dict`` / ``save_dict`` / ``lint`` entrypoint.

ADAPTER_REGISTRY: Dict[str, Tuple[str, str]] = {
    "opendesign.generate.v1": ("daemon.plugin_subsystem.opendesign.generate", "OdGenerate"),
    "opendesign.compose_brief.v1": ("daemon.plugin_subsystem.opendesign.compose_brief", "OdComposeBrief"),
    "opendesign.save.v1": ("daemon.plugin_subsystem.opendesign.save", "OdSave"),
    "opendesign.lint.v1": ("daemon.plugin_subsystem.opendesign.lint", "OdLint"),
}


def register_adapter(adapter_id: str, module_path: str, class_name: str) -> None:
    """Register an adapter binding at runtime.

    Plugin #2+ uses this to declare new ``adapter_id`` → module
    bindings. Boot wiring at ``daemon/manager.py`` calls this in the
    same order as the plugin registration (no side-effects; the
    table is consulted at tool-build time).

    A duplicate ``adapter_id`` overwrites the prior binding (the
    last registration wins) — useful for test fixtures that swap the
    adapter; production callers should register exactly once per
    ``adapter_id``.
    """
    ADAPTER_REGISTRY[adapter_id] = (module_path, class_name)


# ---------------------------------------------------------------------------
# Adapter dispatch — import the adapter module and grab the class
# ---------------------------------------------------------------------------


def _resolve_adapter(adapter_id: str) -> Any:
    """Resolve an ``adapter_id`` → adapter class via :data:`ADAPTER_REGISTRY`.

    Returns the class object; raises ``KeyError`` when the
    ``adapter_id`` is not registered, ``ImportError`` /
    ``AttributeError`` when the binding is malformed (test fixtures
    catch both).
    """
    if adapter_id not in ADAPTER_REGISTRY:
        raise KeyError(
            f"plugin_tool_factory: adapter_id {adapter_id!r} is not registered "
            f"(ADAPTER_REGISTRY keys: {sorted(ADAPTER_REGISTRY)})"
        )
    module_path, class_name = ADAPTER_REGISTRY[adapter_id]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _adapter_callable(adapter_cls: Any, port: Port) -> Callable[..., Dict[str, Any]]:
    """Resolve the adapter's per-Port callable.

    Convention: adapters expose ``<ClassName>.execute_dict(raw: dict,
    ...)`` for the ``execute`` family and ``compose_dict`` /
    ``save_dict`` for the pure-function families. The factory picks
    the right callable based on the ``adapter_id`` (a stable
    one-callable-per-class convention; per-capability method names
    are stable for the v1 surface).
    """
    # Map adapter_id → method name on the adapter class.
    method_map = {
        "opendesign.generate.v1": "execute_dict",
        "opendesign.compose_brief.v1": "compose_dict",
        "opendesign.save.v1": "save_dict",
        "opendesign.lint.v1": "lint",
    }
    method_name = method_map.get(port.adapter_id)
    if method_name is None:
        raise KeyError(
            f"plugin_tool_factory: no method mapping for adapter_id "
            f"{port.adapter_id!r}; expected one of {sorted(method_map)}"
        )
    method = getattr(adapter_cls, method_name, None)
    if method is None:
        raise AttributeError(
            f"plugin_tool_factory: adapter class {adapter_cls.__name__} "
            f"does not expose {method_name!r}"
        )
    return method


# ---------------------------------------------------------------------------
# Per-Port tool builder
# ---------------------------------------------------------------------------


def build_tools_for_port(
    port: Port,
    *,
    env: Optional[Mapping[str, str]] = None,
    project_root: Optional[Any] = None,
) -> List[Any]:
    """Build the native tool(s) backing one Port. Returns 0 or 1 tools
    (a Port maps to exactly one tool).

    The factory refuses to build when:

    - The three-role seam gate fails (CON §3).
    - The ``adapter_id`` is not in :data:`ADAPTER_REGISTRY`.

    Refusal surfaces as a raised ``ValueError`` (caller catches; the
    boot scan logs the refusal and continues — the tool simply does
    not exist for that Port).

    The ``env`` parameter is forwarded to ``execute_dict`` for the
    generate family (the LLM client reads OPENAI_* env vars). The
    ``project_root`` is forwarded to ``save_dict`` (the canonical
    mockup path is rooted there). Both default to ``None`` (the
    adapters read ``os.environ`` and ``os.getcwd()`` respectively).
    """
    gate = seam_gate_check(port)
    if not gate.ok:
        raise ValueError(
            f"plugin_tool_factory: port {port.port_id!r} failed seam gate "
            f"(failures: {[f.code for f in gate.failures]})"
        )

    adapter_cls = _resolve_adapter(port.adapter_id)
    adapter_callable = _adapter_callable(adapter_cls, port)

    # Build the LangChain tool via ``StructuredTool.from_function()``
    # so we can attach the Port's inputs_schema-shaped args_schema.
    # The agent-tool lane consumes the tool by ``name`` (we set
    # ``name = port.port_id`` for the agent-side reference).
    try:
        from langchain_core.tools import StructuredTool  # noqa: PLC0415
    except ImportError:  # pragma: no cover - langchain_core is a project dependency
        raise RuntimeError("langchain_core is required for the plugin tool factory")

    # The tool's ``func`` is a closure over the adapter callable +
    # port_id + env/project_root. The agent invokes the tool with
    # keyword-arg named fields (langchain_core maps the args_schema to
    # the tool's input).
    from daemon.plugin_subsystem.capability_seam_gate import _has_role_consumer  # noqa: PLC0415

    if not _has_role_consumer(port):
        # Defense-in-depth; the seam gate already covered it.
        raise ValueError(
            f"plugin_tool_factory: port {port.port_id!r} has no consumer — refusing"
        )

    tool_name = port.port_id

    # Description = the Port's capability_tags (catalog-only per CON §3)
    # joined with the consumer names so the agent's tool-help output
    # surfaces "what this Port does + who calls it". The full
    # description is verbose on purpose — the agent needs the surface.
    description = (
        f"Plugin tool for Port {port.port_id!r} (v{port.version}); "
        f"adapter_id={port.adapter_id!r}, path={port.provider_path}; "
        f"consumers={list(port.consumers)}, "
        f"capability_tags={sorted(port.capability_tags)}. "
        f"Provider internals are EVOLVABLE behind the frozen Port contract."
    )

    # Forward env/project_root into the callable's closure. The
    # ``env`` mapping is read at call time so test fixtures can swap
    # OPENAI_* vars via the wrapper without re-instantiating the tool.
    def _tool_fn(**kwargs: Any) -> Dict[str, Any]:
        raw: Dict[str, Any] = dict(kwargs)
        # Drop None-valued optionals so the adapter's defaults take
        # over (the Port inputs_schema defines defaults; the adapter
        # honors them when the field is absent).
        for k in list(raw.keys()):
            if raw[k] is None:
                del raw[k]
        try:
            if port.adapter_id == "opendesign.save.v1":
                # save needs a project_root (the canonical mockup path
                # is rooted at the project's working directory).
                from pathlib import Path
                pr = Path(project_root) if project_root is not None else Path.cwd()
                return adapter_callable(raw, project_root=pr)  # type: ignore[arg-type]
            if port.adapter_id == "opendesign.generate.v1":
                return adapter_callable(raw, env=env)  # type: ignore[arg-type]
            return adapter_callable(raw)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - adapter surface returns CON §3 envelopes on failure
            # Adapter failures already produce CON §3 error envelopes;
            # the agent-tool lane expects a string-shaped response.
            # We surface a structured JSON string so the agent can
            # parse the envelope.
            import json
            envelope = {
                "ok": False,
                "code": "adapter_internal_error",
                "message": str(exc),
            }
            return {"raw_error": envelope, "json": json.dumps(envelope)}

    tool = StructuredTool.from_function(
        func=_tool_fn,
        name=tool_name,
        description=description,
        # The args_schema is the Port's inputs_schema verbatim (a
        # JSON-Schema dict). langchain_core accepts dict-shaped
        # args_schema and converts it into the tool's input shape.
        args_schema=port.inputs_schema,
    )
    return [tool]


# ---------------------------------------------------------------------------
# Aggregate builder — build for every Port in the registry
# ---------------------------------------------------------------------------


def build_plugin_tools(
    *,
    env: Optional[Mapping[str, str]] = None,
    project_root: Optional[Any] = None,
    registry: Optional[PortRegistry] = None,
) -> Tuple[List[Any], List[Tuple[str, str]]]:
    """Build native tools for every Port in the registry.

    Returns ``(built_tools, refused_ports)``. The refused_ports list
    is ``[(port_id, refusal_code), ...]`` — the boot scan logs each
    refusal and the agent-tool lane simply does not see those tools.

    The default ``registry`` is :func:`build_default_port_registry`;
    tests pass a hand-rolled registry for fixture scenarios.
    """
    if registry is None:
        registry = build_default_port_registry()
    built: List[Any] = []
    refused: List[Tuple[str, str]] = []
    for port in registry.all():
        try:
            tools = build_tools_for_port(
                port,
                env=env,
                project_root=project_root,
            )
            built.extend(tools)
        except ValueError as exc:
            # Surface the first refusal code for the report (the
            # ValueError message includes the failure codes joined by
            # ', '; we extract the first for the (port_id, code) tuple).
            msg = str(exc)
            # Best-effort parse of the first failure code; fall back
            # to ``"seam_gate_failed"`` when parsing fails.
            first_code = "seam_gate_failed"
            if "failures: [" in msg:
                inside = msg.split("failures: [", 1)[1]
                inside = inside.split("]", 1)[0]
                if inside:
                    first_code = inside.split("'")[1] if "'" in inside else inside.split(",")[0].strip().strip("'\"")
            refused.append((port.port_id, first_code))
    return built, refused