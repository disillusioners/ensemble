"""Vendoring classifier (REC §1.2 component 5 — slice ② + ③).

Classifies ONE plugin ONCE at vendoring time. The classification is the
authoritative answer to: "given this manifest, which integration path
(B / C / A-fenced) is this plugin declaring, and does the
declared path match what the registered path-type row allows for the
declared ``execution_mode``?".

**Scope discipline (REC §1.2 + slice ③ dispatch).**

- A plugin is classified exactly once at vendoring time. The
  classification is deterministic and depends only on the
  :class:`~daemon.plugin_subsystem.plugin_declaration.PluginDeclaration`
  and the :class:`~daemon.plugin_subsystem.path_type_registry.PathTypeRegistry`.
  No I/O, no daemon calls, no time-of-day.
- A misclassification (path-letter vs execution-mode mismatch) is
  detected here and REFUSES THE VENDORING (never the runtime). The
  refusal code is ``misclassified_at_vendoring`` (CON §5).
- The classifier is a pure routing ladder — it has no knowledge of
  upstream git, vendoring-time I/O, or sync-runner state. The
  sync-runner is the consumer of the classification.

**Routing ladder (CON §4 + REC §1.2):**

1. Path letter must be a registered row in
   :class:`~daemon.plugin_subsystem.path_type_registry.PathTypeRegistry`
   (``B`` / ``C`` / ``A-fenced`` / future letters D/E/F).  A
   registered path letter is the FIRST gate; a future letter added
   later is automatically recognized because the registry is data.
2. The row's ``allowed_execution_modes`` set must include the
   plugin's declared ``execution_mode``.  Mismatch ⇒
   ``misclassified_at_vendoring`` (this is the only "misclass" path
   — the manifest's path letter vs the row's allowlist is the
   classifier's whole job).
3. The row's ``required_manifest_fields`` set must be SATISFIED
   (every field present, of the right type).  Mismatch ⇒
   ``misclassified_at_vendoring`` (a B-path row that requires
   ``lifted_symbol`` but the manifest lacks it is
   mis-classified-for-vendoring).
4. The row's ``fence`` flag must be honored: a fenced row
   (``A`` in v1) requires a complete ``fence_grant`` block in
   the manifest.  Mismatch ⇒ ``misclassified_at_vendoring`` (a
   fence-stripped A is a classification refusal, not a runtime
   one).

**ClassifiedFor dataclass.** The output is a flat record of
classification facts. The sync-runner + tool-factory consume this
later; nothing else needs to re-walk the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from daemon.plugin_subsystem.path_type_registry import PathTypeRegistry
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

__all__ = [
    "ClassifiedFor",
    "ClassificationRefusal",
    "classify",
    "ROUTING_LADDER_STEPS",
]


# Ordered list of routing-ladder step names (for tests that want to
# pin the order; the docstring above is the source of truth for the
# semantics).
ROUTING_LADDER_STEPS: tuple[str, ...] = (
    "path_letter_registered",
    "execution_mode_in_row_allowlist",
    "required_manifest_fields_satisfied",
    "fence_honored",
)


@dataclass(frozen=True)
class ClassifiedFor:
    """Output of :func:`classify` for one plugin.

    The classification is recorded once and consumed by the
    sync-runner (slice ③) and the tool-factory (slice ⑤). The
    ``path_type_row`` is the :class:`PathTypeRow` object — not its
    name — so the downstream code does not need to re-query the
    registry for the same plugin.
    """

    plugin_name: str
    path_letter: str
    path_type_row: Any  # PathTypeRow; circular import if typed directly
    execution_mode: str
    fence_required: bool
    fence_granted: bool
    is_classified: bool  # True when no refusal was raised
    routing_ladder_steps: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationRefusal(Exception):
    """Refusal raised by :func:`classify` for a misclassified plugin.

    The refusal code is the FROZEN ``misclassified_at_vendoring`` token
    per CON §5; the message names the specific gate that failed so
    the operator can fix the manifest.

    Misclassification ALWAYS refuses the VENDORING (never the
    runtime) — this is the discipline that prevents behavior
    plugins from being silently treated as data (REC §1.2 row 3,
    "positive execution_mode + vendoring-time classification +
    smoke fixture; misclassification fails the VENDORING, never
    the runtime").
    """

    code: str = "misclassified_at_vendoring"
    message: str = ""
    failed_step: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] step={self.failed_step!r} {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "failed_step": self.failed_step,
        }


def _step_path_letter_registered(decl: PluginDeclaration, registry: PathTypeRegistry) -> Optional[str]:
    if not registry.is_registered(decl.integration_path):
        registered = ", ".join(registry.registered_paths())
        return (
            f"integration_path {decl.integration_path!r} is not a registered path-type row "
            f"(registered: {registered})"
        )
    return None


def _step_execution_mode_in_row_allowlist(
    decl: PluginDeclaration, registry: PathTypeRegistry
) -> Optional[str]:
    row = registry.get(decl.integration_path)
    if decl.execution_mode not in row.allowed_execution_modes:
        allowed = ", ".join(row.allowed_execution_modes)
        return (
            f"execution_mode {decl.execution_mode!r} is not in the row's allowed_modes "
            f"for path {decl.integration_path!r} (allowed: {allowed})"
        )
    return None


def _step_required_manifest_fields_satisfied(
    decl: PluginDeclaration, registry: PathTypeRegistry
) -> Optional[str]:
    row = registry.get(decl.integration_path)
    # Use the plugin's __dict__ view (dataclasses expose field names
    # via __dataclass_fields__ but we want a flat presence check).
    decl_dict: Mapping[str, Any] = {
        "lifted_symbol": decl.lifted_symbol,
        "entrypoint": decl.entrypoint,
        "ipc_version": decl.ipc_version,
        "hosted_runtime_deps": decl.hosted_runtime_deps,
        "fence_grant": decl.fence_grant,
    }
    missing = [f for f in row.required_manifest_fields if decl_dict.get(f) in (None, "", [], {})]
    if missing:
        return (
            f"path {decl.integration_path!r} requires manifest field(s): {', '.join(missing)}"
        )
    return None


def _step_fence_honored(decl: PluginDeclaration, registry: PathTypeRegistry) -> Optional[str]:
    row = registry.get(decl.integration_path)
    if not row.fence:
        return None  # not a fenced row; nothing to check
    grant = decl.fence_grant or {}
    incomplete = [
        f for f in ("rationale", "granted_by", "granted_at") if not grant.get(f)
    ]
    if incomplete:
        return (
            f"path {decl.integration_path!r} is fence-required; fence_grant is incomplete: "
            f"{', '.join(incomplete)}"
        )
    return None


_STEPS = (
    _step_path_letter_registered,
    _step_execution_mode_in_row_allowlist,
    _step_required_manifest_fields_satisfied,
    _step_fence_honored,
)


def classify(
    declaration: PluginDeclaration, registry: PathTypeRegistry
) -> ClassifiedFor:
    """Classify ONE plugin ONCE at vendoring time (the routing ladder).

    The classification runs every step in order. The first step that
    produces a non-None message raises :class:`ClassificationRefusal`;
    the caller (sync-runner or schema-CI) catches the refusal and
    refuses the VENDORING.  Successful classification returns a
    :class:`ClassifiedFor` with the routing-ladder step list (so
    downstream code can introspect what was checked).

    Note: this is a PURE function over the declaration + registry.
    It does not touch upstream git, the filesystem, or the
    daemon. The manifest reader (slice ①) is the gate that
    produces the declaration; the classifier is the second gate
    (path-letter vs execution-mode vs row-allowlist).
    """
    completed: list[str] = []
    for step in _STEPS:
        msg = step(declaration, registry)
        step_name = step.__name__.removeprefix("_step_")
        if msg is not None:
            raise ClassificationRefusal(message=msg, failed_step=step_name)
        completed.append(step_name)

    row = registry.get(declaration.integration_path)
    return ClassifiedFor(
        plugin_name=declaration.name,
        path_letter=declaration.integration_path,
        path_type_row=row,
        execution_mode=declaration.execution_mode,
        fence_required=row.fence,
        fence_granted=bool(declaration.fence_grant),
        is_classified=True,
        routing_ladder_steps=tuple(completed),
    )
