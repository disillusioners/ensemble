"""Process-level registry that memoises the pydantic ``args_schema`` of ``@tool``.

WHY THIS EXISTS (ROW2-DEEP-DIVE §3 — the flagship 23.16 s event-loop stall)
---------------------------------------------------------------------------
``langchain_core.tools.tool`` builds a FRESH pydantic model class every time a
tool function is *decorated*:

* ``create_schema_from_function`` (``langchain_core/tools/base.py:289``) runs
  the pydantic v1-compat ``validate_arguments`` shim, which builds TWO model
  classes (``pydantic/deprecated/decorator.py:243`` and ``:284``), and then
  builds a THIRD (the subset model, ``langchain_core/utils/pydantic.py:231``).

The daemon's tool factories define their ``@tool`` functions INSIDE the
factory body, so the decoration — and therefore the schema — is re-run on
EVERY graph assembly.  Measured on this tree: ~640 model classes per
``create_instance_tools`` call.

There is a SECOND, larger cost the first fix must also cover: langchain's
``BaseTool.tool_call_schema`` is a plain ``@property`` (NOT a
``cached_property``) that rebuilds a subset model on EVERY access, and
``convert_to_openai_tool`` touches it FOUR times per tool.  With three
``bind_tools`` sites that is another ~12 model classes per tool per assembly.

The schema is a PURE function of the function's signature and docstring — it
does NOT depend on the closure (the ``manager`` / ``instance_id`` a tool
captures).  This module therefore memoises:

* the built ``args_schema`` model class, per tool FUNCTION, and
* the derived OpenAI JSON-schema payload, per tool IDENTITY,

and hands the cached artefacts back to langchain, so the pydantic work
happens ONCE per process instead of once per graph assembly.

INVARIANTS (ROW2-DEEP-DIVE §3.4 — all six are load-bearing)
-----------------------------------------------------------
1. **Byte-identical provider payload.**  The cached class IS the class
   langchain built; the rebuild re-uses langchain's own ``name`` /
   ``description`` / ``return_direct`` / ``response_format`` / ``extras``.
2. **Tool names are frozen** (``test/packs/frozen_tool_name_discovery_unit_test.sh``):
   the rebuild re-uses the name langchain computed, it never derives one.
3. **Only the SCHEMA is cached, never the tool object.**  The closure stays
   per-instance; nothing mutates ``args_schema`` after construction, so the
   shared class cannot leak state across instances.
4. **MCP tools stay dynamic.**  MCP tools are not decorated through this
   module, and the OpenAI payload cache is keyed on the ``args_schema`` CLASS
   OBJECT: a re-discovered MCP schema is a new class, hence a new cache key,
   hence a freshly computed payload.  ``daemon/tools/infra.py``'
   ``_invalidate_mcp_schema_cache`` semantics are untouched.
5. **Tool-filter semantics are unchanged** — ``resolve_tool_filter`` still
   decides exposure; the registry only memoises schemas.
6. **Factory side effects are NOT skipped.**  The factories still run on every
   assembly; only the pydantic class construction *inside the decorator* is
   memoised.  (Several factories DO read the DB at build time — e.g.
   ``manager._instance_repository.get(...)`` — and that behaviour is
   preserved verbatim.)

HOW TO USE IT
-------------
Swap the decorator import in the tool modules::

    from ._schema_registry import tool          # instead of langchain_core.tools

and let :func:`daemon.graph.build_instance_llms` bind the memoised OpenAI
payload via :func:`openai_tool_payloads`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Sequence

from langchain_core.tools import StructuredTool
from langchain_core.tools import tool as _langchain_tool

logger = logging.getLogger(__name__)

__all__ = [
    "tool",
    "openai_tool_payloads",
    "registry_stats",
    "clear_registry",
    "warm_schema_registry",
]


# ---------------------------------------------------------------------------
# Registry state
# ---------------------------------------------------------------------------


class _Entry:
    """The memoised artefacts for ONE decorated tool function."""

    __slots__ = (
        "args_schema",
        "name",
        "description",
        "return_direct",
        "response_format",
        "extras",
        "is_coroutine",
        "fingerprint",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))


_LOCK = threading.RLock()

# (module, qualname) -> _Entry
_ENTRIES: dict[tuple[str, str], _Entry] = {}

# payload cache key -> OpenAI tool payload dict
_PAYLOADS: dict[tuple, dict] = {}

# Hard cap on the payload cache.  The daemon's own surface is a few hundred
# entries; the cap only exists so a pathological MCP churn cannot grow the
# dict without bound.
_PAYLOAD_CAP = 4096

_STATS = {
    "schema_builds": 0,     # pydantic classes built (cache misses)
    "schema_hits": 0,       # decorator calls served from the cache
    "payload_misses": 0,    # OpenAI payloads computed
    "payload_hits": 0,      # OpenAI payloads served from the cache
}


# ---------------------------------------------------------------------------
# Fingerprinting — the guard that makes the memoisation safe
# ---------------------------------------------------------------------------


def _registry_key(fn: Callable) -> tuple[str, str]:
    """The stable per-FUNCTION key: ``(module, qualname)``.

    A nested tool's qualname embeds its factory (``create_x.<locals>.y``), so
    two different factories never collide, and two invocations of the SAME
    factory always do.
    """
    return (getattr(fn, "__module__", "") or "", getattr(fn, "__qualname__", "") or "")


def _fingerprint(fn: Callable) -> tuple:
    """A cheap, stable fingerprint of everything the schema depends on.

    The pydantic schema is derived from the function's SIGNATURE (parameter
    names, annotations, defaults) and its DOCSTRING.  For every ``@tool``
    function in ``daemon/tools/`` the annotations are source-determined
    (builtin types, ``Literal[...]`` and ``Field(...)`` literals — verified by
    AST census), so the CODE OBJECT identity plus the docstring plus the
    defaults is a complete fingerprint.  ``fn.__code__`` is the very same
    object across every invocation of the factory, so this comparison is
    stable across instances.

    Deliberately NOT used: ``repr(fn.__annotations__)`` — ``Field(...)``
    builds a fresh ``FieldInfo`` on every call, so its repr is not stable.
    """
    return (
        getattr(fn, "__code__", None),
        getattr(fn, "__doc__", None),
        getattr(fn, "__defaults__", None),
        getattr(fn, "__kwdefaults__", None),
    )


def _fingerprint_matches(left: tuple, right: tuple) -> bool:
    try:
        return bool(left == right)
    except Exception:  # pragma: no cover - exotic default values
        return False


def _schema_equivalent(cached: Any, fresh: Any) -> bool:
    """Cheap equivalence probe for a CALLER-SUPPLIED ``args_schema``.

    The bare ``@tool`` form infers its schema from the function, so the
    fingerprint above is a complete guard.  The ``@tool(args_schema=Model)``
    form receives the schema from the caller — and several factories declare
    that model INSIDE the factory body, so a NEW (content-identical) class is
    built on every invocation.  This probe answers "is the fresh class
    content-equivalent to the memoised one?" by comparing the field set, the
    required/optional split and the per-field defaults.

    A mismatch (a conditional field, a changed default) forces a rebuild, so
    the memoisation can never serve a stale schema.
    """
    if cached is fresh:
        return True
    if fresh is None:
        # The caller dropped the schema since the memoised build.
        return False
    try:
        cached_fields = cached.model_fields
        fresh_fields = fresh.model_fields
    except AttributeError:
        return False
    if set(cached_fields) != set(fresh_fields):
        return False
    for name, field in cached_fields.items():
        other = fresh_fields[name]
        if field.is_required() != other.is_required():
            return False
        try:
            if repr(field.default) != repr(other.default):
                return False
        except Exception:  # pragma: no cover - exotic defaults
            return False
    return True


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def _build_with_langchain(fn: Callable, lc_args: tuple, lc_kwargs: dict, bare: bool):
    """Run langchain's own decorator — the exact code path of the base tree."""
    if bare:
        # ``@tool`` — langchain's ``tool(fn)`` returns the TOOL, not a partial.
        return _langchain_tool(fn)
    return _langchain_tool(*lc_args, **lc_kwargs)(fn)


def _remember(fn: Callable, key: tuple[str, str], fingerprint: tuple, built: Any) -> None:
    """Memoise the schema langchain just built for ``fn``."""
    schema = getattr(built, "args_schema", None)
    if schema is None:
        # ``infer_schema=False`` produces a schema-less ``Tool``; there is
        # nothing to memoise (and nothing expensive to memoise either).
        return
    entry = _Entry(
        args_schema=schema,
        name=getattr(built, "name", None),
        description=getattr(built, "description", None),
        return_direct=bool(getattr(built, "return_direct", False)),
        response_format=getattr(built, "response_format", "content"),
        extras=dict(getattr(built, "extras", None) or {}),
        is_coroutine=getattr(built, "coroutine", None) is not None,
        fingerprint=fingerprint,
    )
    with _LOCK:
        _ENTRIES[key] = entry
    _STATS["schema_builds"] += 1


def _rebuild(fn: Callable, entry: _Entry):
    """Rebuild the tool with the CACHED schema.

    Mirrors langchain's ``StructuredTool.from_function`` call exactly, except
    that ``args_schema`` is supplied instead of inferred — which is precisely
    the step that costs three pydantic model classes per tool per assembly.
    """
    kwargs = dict(
        name=entry.name,
        description=entry.description,
        return_direct=entry.return_direct,
        args_schema=entry.args_schema,
        response_format=entry.response_format,
        extras=entry.extras,
    )
    if entry.is_coroutine:
        return StructuredTool.from_function(func=None, coroutine=fn, **kwargs)
    return StructuredTool.from_function(func=fn, coroutine=None, **kwargs)


def _decorate(fn: Callable, lc_args: tuple, lc_kwargs: dict, bare: bool):
    """Decorate ``fn`` with the memoised schema."""
    key = _registry_key(fn)
    fingerprint = _fingerprint(fn)
    with _LOCK:
        entry = _ENTRIES.get(key)
    if entry is not None and _fingerprint_matches(entry.fingerprint, fingerprint):
        # For the bare form the schema is INFERRED from the function, so the
        # fingerprint is a complete guard.  For the ``args_schema=`` form the
        # schema comes from the caller and must be probed for equivalence.
        caller_schema = lc_kwargs.get("args_schema")
        if bare or caller_schema is None or _schema_equivalent(entry.args_schema, caller_schema):
            _STATS["schema_hits"] += 1
            return _rebuild(fn, entry)

    built = _build_with_langchain(fn, lc_args, lc_kwargs, bare)
    _remember(fn, key, fingerprint, built)
    return built


def tool(*args: Any, **kwargs: Any) -> Any:
    """Drop-in replacement for :func:`langchain_core.tools.tool`.

    Same call shapes as langchain's decorator:

    * ``@tool`` — the bare form (the daemon's dominant shape),
    * ``@tool("name")`` — an explicit tool name,
    * ``@tool(args_schema=Model)`` — a caller-supplied schema,
    * ``@tool(parse_docstring=True)`` — keyword options.

    The ONLY behavioural difference is that the pydantic ``args_schema`` class
    is memoised per tool function instead of being rebuilt on every factory
    invocation.  The class that is cached is the one langchain itself built,
    so the provider payload is byte-identical.
    """
    if (
        len(args) == 1
        and not kwargs
        and callable(args[0])
        and hasattr(args[0], "__name__")
    ):
        # Bare ``@tool``.
        return _decorate(args[0], (), {}, True)

    if args and isinstance(args[0], str):
        # ``@tool("name")`` / ``@tool("name", parse_docstring=True)``.
        name = args[0]
        if len(args) > 1:
            raise ValueError("Too many arguments for tool decorator.")

        def _partial_with_name(fn: Callable):
            return _decorate(fn, (name,), dict(kwargs), False)

        return _partial_with_name

    if args and callable(args[0]):
        # ``@tool(some_runnable)`` — the Runnable form.  The daemon does not
        # use it; defer to langchain verbatim (no schema to memoise).
        return _langchain_tool(*args, **kwargs)

    # ``@tool(args_schema=...)`` / ``@tool(parse_docstring=True)``.
    def _partial(fn: Callable):
        return _decorate(fn, (), dict(kwargs), False)

    return _partial


# ---------------------------------------------------------------------------
# OpenAI payload memoisation
# ---------------------------------------------------------------------------


def _payload_key(tool_obj: Any) -> tuple | None:
    """The cache key for a tool's OpenAI payload.

    The payload is a pure function of ``(name, description, args_schema)``:
    ``_format_tool_to_openai_function`` derives the parameters from
    ``tool.tool_call_schema`` (a subset model of ``args_schema`` named after
    the tool and documented with the tool's description).

    Keying on the ``args_schema`` CLASS OBJECT keeps the MCP lane dynamic: a
    re-discovered MCP schema is a NEW class, hence a NEW key, hence a freshly
    computed payload — no stale schema can survive an MCP invalidation.
    """
    name = getattr(tool_obj, "name", None)
    description = getattr(tool_obj, "description", None)
    schema = getattr(tool_obj, "args_schema", None)
    if schema is None or isinstance(schema, dict):
        # A dict schema is unhashable and (in practice) MCP-provided; do not
        # memoise it.
        return None
    try:
        hash(schema)
    except TypeError:  # pragma: no cover - exotic schema objects
        return None
    return (name, description, schema)


def openai_tool_payloads(tools: Sequence[Any]) -> list[dict]:
    """Return the OpenAI tool payload for ``tools`` — memoised per tool.

    This is the S2 lever: the payload is computed ONCE per tool (instead of
    once per ``bind_tools`` site) and the SAME dicts are handed to every
    binding, so the provider sees a byte-identical payload while langchain
    stops rebuilding a subset model per tool per site.
    """
    from langchain_core.utils.function_calling import convert_to_openai_tool

    payloads: list[dict] = []
    for tool_obj in tools:
        key = _payload_key(tool_obj)
        if key is None:
            payloads.append(convert_to_openai_tool(tool_obj))
            continue
        with _LOCK:
            hit = _PAYLOADS.get(key)
        if hit is not None:
            _STATS["payload_hits"] += 1
            payloads.append(hit)
            continue
        payload = convert_to_openai_tool(tool_obj)
        with _LOCK:
            if len(_PAYLOADS) >= _PAYLOAD_CAP:
                # Bounded growth: drop the oldest half (dict insertion order).
                for stale in list(_PAYLOADS.keys())[: _PAYLOAD_CAP // 2]:
                    _PAYLOADS.pop(stale, None)
            _PAYLOADS[key] = payload
        _STATS["payload_misses"] += 1
        payloads.append(payload)
    return payloads


# ---------------------------------------------------------------------------
# Introspection + lifecycle
# ---------------------------------------------------------------------------


def registry_stats() -> dict[str, int]:
    """A snapshot of the registry counters (used by the pack + unit tests)."""
    with _LOCK:
        return {
            "entries": len(_ENTRIES),
            "payloads": len(_PAYLOADS),
            **_STATS,
        }


def clear_registry() -> None:
    """Drop every memoised artefact (test-only helper)."""
    with _LOCK:
        _ENTRIES.clear()
        _PAYLOADS.clear()
    for key in _STATS:
        _STATS[key] = 0


def clear_payload_cache() -> None:
    """Drop only the memoised OpenAI payloads (test-only helper).

    The schema entries survive, so the next payload derivation still costs
    nothing on the schema side — which is exactly the state the "bind once"
    pin needs to observe a cold payload derivation.
    """
    with _LOCK:
        _PAYLOADS.clear()
    _STATS["payload_misses"] = 0
    _STATS["payload_hits"] = 0


# ---------------------------------------------------------------------------
# Boot warm
# ---------------------------------------------------------------------------


class _Inert:
    """A permissive, side-effect-free stand-in for anything a tool factory
    touches while BUILDING its tools.

    Every attribute access returns the shared inert singleton; every call
    returns it too.  It is FALSY and EMPTY so that "not found" branches (the
    common shape for ``manager._instance_repository.get(...)``) behave like a
    miss instead of exploding.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _INERT

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _INERT

    def __getitem__(self, item: Any) -> Any:
        return _INERT

    def __iter__(self):
        return iter(())

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def __str__(self) -> str:
        return "<schema-registry-inert>"

    def __repr__(self) -> str:
        return "<schema-registry-inert>"


_INERT = _Inert()


class _InertConfig:
    """The config shape the tool factories read at BUILD time."""

    class _Daemon:
        port = 0
        host = "127.0.0.1"

    class _LLM:
        allowed_models: list = []
        model = ""
        base_url = ""

    class _Agents:
        pass

    def __init__(self) -> None:
        self.daemon = _InertConfig._Daemon()
        self.llm = _InertConfig._LLM()
        self.agents = _InertConfig._Agents()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _INERT


class _InertManager:
    """A manager-shaped object with NO database and NO live state behind it.

    The boot warm runs BEFORE the daemon owns a manager (it starts in
    ``daemon/__main__.py``, ahead of the uvicorn lifespan), so the warm can
    never touch live manager or DB state: it gets this inert stand-in, builds
    the tool surface once to memoise every schema, and throws the tools away.
    """

    def __init__(self) -> None:
        self.config = _InertConfig()
        self.ensemble_config = _InertConfig()
        self.project_store = _INERT
        self._mcp_service = None
        self._instance_repository = _Inert()
        self._project_repository = _Inert()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _INERT


def warm_schema_registry() -> int:
    """Warm the registry by building the tool surface once, off to the side.

    Returns the number of registry entries added by this call.  The call is
    BEST EFFORT: a factory that refuses to build against the inert manager
    aborts the warm, and whatever was memoised before that point is kept.
    """
    # Local import: ``daemon.tools.instance`` imports this module.
    from .instance import create_instance_tools

    with _LOCK:
        before = len(_ENTRIES)

    try:
        create_instance_tools(_InertManager(), "schema-registry-warm", "")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "[schema-registry] boot warm stopped early (%s: %s); "
            "%d schema(s) memoised before the stop",
            type(exc).__name__,
            exc,
            len(_ENTRIES) - before,
        )

    with _LOCK:
        warmed = len(_ENTRIES) - before
    logger.info(
        "[schema-registry] boot warm complete: %d schema(s) memoised", warmed
    )
    return warmed
