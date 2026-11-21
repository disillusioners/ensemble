"""Tools-node observation tap + off-loop counter flusher (Phase 1).

This module is the measurement slice's hot path. It is STRICTLY
observation-only: it never changes the tool call's result, never raises
into the tools node, and never performs DB I/O on the event loop.

FROZEN CONTRACTS (decisions.md D3/D14/D15)
------------------------------------------
* Sink signature — ``Callable[[str, str | None, str, str, bool], None]``
  = ``(tool_name, tool_category, agent_id, project_id, ok)``. There is NO
  argument slot: the NAMES-ONLY guarantee at the type level. Phase 2
  consumes EXACTLY this signature with ZERO phase-1 edits.
* Registration symbols are frozen as ``register_sink`` / ``unregister_sink``
  (module level, thread-safe, idempotent).
* Chain placement: the wrapper returned by :func:`wrap_tools_node` sits
  BETWEEN ``compact_note`` (outermost) and ``wrapped_tools_node``. It must
  see the ToolNode RESULT to set ``ok`` (``ToolMessage.status != "error"``,
  default True on ambiguity).
* A-1: identity resolves via ``manager._instance_repository``.
* A-2: identity resolves ONCE per node invocation (per batch), never per
  tool call; sinks fire ONLY from this async wrapper on the main loop.
* A-3: single 256-char choke on tool_name / agent_id / project_id before
  enqueue, with an overflow counter + throttled WARNING.
* A-5/A-6: counter map + dirty set, NO hard cap, emergency flush at
  >=10,000 dirty keys, NEVER drop counters, flush cadence 30 s / >=500.
* A-5b: the tap gates at enqueue (call-time); the FLUSHER IS SWITCH-BLIND
  (final tail flush then idle — drain-and-discard is REJECTED).
* A-5c: explicit bounded 10 s drain at shutdown, NOT via
  ``manager._background_tasks``.
* A-5d: respawn-on-death outer task + canary counters + throttled WARNING.

COUNTING CONTRACT (frozen verbatim — see decisions.md D15): counters are
AT-LEAST-ONCE per EXECUTED tool_call; duplicates from retries are accepted,
not deduplicated. See the module-level ``COUNTING_CONTRACT`` constant.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from langchain_core.runnables import RunnableConfig

from daemon.repositories.tool_usage.models import NONE_PROJECT, UNKNOWN_AGENT

if TYPE_CHECKING:
    from daemon.repositories.tool_usage.repository import ToolUsageRepository

logger = logging.getLogger(__name__)

COUNTING_CONTRACT = (
    "Tool-usage counters are counted AT-LEAST-ONCE, per EXECUTED tool_call, "
    "at the tools node, on the main event loop. Duplicates from retries / "
    "checkpoint resumes are accepted, not deduplicated."
)

# ── Frozen bounds / cadence (A-5, A-5a, A-3) ───────────────────────────────
NAME_CHOKE_CHARS = 256
FLUSH_INTERVAL_S = 30.0
FLUSH_SIZE_TRIGGER = 500
EMERGENCY_DIRTY_KEYS = 10_000
DRAIN_BOUND_S = 10.0
WARN_THROTTLE_S = 300.0  # 1 / 5 min during outages (A-5d)

# ── Collection kill-switch (D7 / §3.6) ─────────────────────────────────────
COLLECTION_KEY = "limits.tool_usage_collection_enabled"
COLLECTION_ENV_VAR = "LIMITS_TOOL_USAGE_COLLECTION_ENABLED"
DEFAULT_COLLECTION_ENABLED = True
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# ── Frozen sink signature ──────────────────────────────────────────────────
# positional: (tool_name, tool_category, agent_id, project_id, ok)
ToolUsageSink = Callable[[str, str | None, str, str, bool], None]


@dataclass(frozen=True)
class ToolUsageEvent:
    """A single observation event (value type; ``day`` set at enqueue).

    Reserved for Phase 2.
    """

    tool_name: str
    tool_category: str | None
    agent_id: str
    project_id: str
    ok: bool
    day: str


# ── Throttled WARNING helper (A-3 / A-5d) ──────────────────────────────────
_last_warning_at: dict[str, float] = {}
_warning_lock = threading.Lock()


def _throttled_warning(key: str, message: str, *args: Any) -> None:
    now = time.monotonic()
    with _warning_lock:
        last = _last_warning_at.get(key, float("-inf"))
        if now - last < WARN_THROTTLE_S:
            return
        _last_warning_at[key] = now
    logger.warning(message, *args)


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _choke(value: str, label: str, overflow: "ToolUsageFlusher | None") -> str:
    """A-3: truncate to 256 chars; bump overflow counter + throttled WARNING."""
    if len(value) <= NAME_CHOKE_CHARS:
        return value
    if overflow is not None:
        overflow.note_overflow()
    _throttled_warning(
        f"choke:{label}",
        "[ToolUsage] %s exceeded %d chars (truncated at the tap)",
        label,
        NAME_CHOKE_CHARS,
    )
    return value[:NAME_CHOKE_CHARS]


# ── Tool category resolution (static map from the registry) ────────────────
_category_map: dict[str, str] | None = None
_category_lock = threading.Lock()


def resolve_tool_category(tool_name: str) -> str | None:
    """Resolve a tool's declared category from the registry's static map.

    Unmapped ⇒ ``None`` (null in JSON), never a crash (R6). The map is
    built once from the tool registry's declared category — the same
    source the FE / agent tool catalog uses (§3.5).
    """
    global _category_map
    if _category_map is None:
        with _category_lock:
            if _category_map is None:
                built: dict[str, str] = {}
                try:
                    from daemon.tools import _tool_registry as registry

                    for name, meta in registry.list_tools().items():
                        category = meta.get("category")
                        if category:
                            built[str(name)] = str(category)
                except Exception:  # pragma: no cover - defensive degradation
                    built = {}
                _category_map = built
    return _category_map.get(tool_name)


# ── Sink registry (module-level, thread-safe, idempotent) ──────────────────
_sinks: set[ToolUsageSink] = set()
_sinks_lock = threading.Lock()


def register_sink(sink: ToolUsageSink) -> None:
    """Register a sink (idempotent). FROZEN symbol — Phase 2 consumes it."""
    with _sinks_lock:
        _sinks.add(sink)


def unregister_sink(sink: ToolUsageSink) -> None:
    """Unregister a sink (idempotent, never raises)."""
    with _sinks_lock:
        _sinks.discard(sink)


def _snapshot_sinks() -> tuple[ToolUsageSink, ...]:
    with _sinks_lock:
        return tuple(_sinks)


# ── Collection kill-switch read (call-time, fail-closed — D7) ──────────────
def is_collection_enabled() -> bool:
    """Resolve the collection kill-switch at CALL TIME, fail-closed.

    * env var explicitly set ⇒ truthy set ⇒ ON, ANY other value ⇒ OFF;
    * env var absent ⇒ managed-key live cache value (bool) honored;
    * key absent from the cache ⇒ code default (ON — opt-out knob);
    * present but unparseable/unknown ⇒ OFF (fail-closed, never a crash).
    """
    raw_env = os.environ.get(COLLECTION_ENV_VAR)
    if raw_env is not None:
        return raw_env.strip().lower() in _TRUTHY
    try:
        from daemon.services.metrics_gate import live_managed_value

        value = live_managed_value(COLLECTION_KEY, DEFAULT_COLLECTION_ENABLED)
    except Exception:  # pragma: no cover - never crash the hot path
        return DEFAULT_COLLECTION_ENABLED
    if isinstance(value, bool):
        return value
    if value is None:
        return DEFAULT_COLLECTION_ENABLED
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


# ── Counter-map flusher (A-5/A-5a–d) ───────────────────────────────────────
_CounterKey = tuple[str, str, str, str]  # (day, project_id, tool_name, agent_id)


class ToolUsageFlusher:
    """Owns the counter map + dirty set and drains them off-loop.

    The ledger sink (:meth:`ledger_sink`) has the frozen sink signature and
    is what the tap fans out to; it increments the map (no DB I/O). The
    supervisor task flushes deltas via ``asyncio.to_thread`` and respawns
    on death. It is SWITCH-BLIND — the tap gates enqueue; the flusher just
    drains whatever is dirty (so flipping the switch OFF yields one final
    tail flush, then idles).
    """

    def __init__(
        self,
        repository: "ToolUsageRepository",
        *,
        interval_s: float = FLUSH_INTERVAL_S,
        size_trigger: int = FLUSH_SIZE_TRIGGER,
        emergency_at: int = EMERGENCY_DIRTY_KEYS,
        drain_bound_s: float = DRAIN_BOUND_S,
    ) -> None:
        self._repo = repository
        self._interval_s = interval_s
        self._size_trigger = size_trigger
        self._emergency_at = emergency_at
        self._drain_bound_s = drain_bound_s
        self._counts: dict[_CounterKey, int] = {}
        self._dirty: set[_CounterKey] = set()
        self._lock = threading.Lock()
        self._stop: asyncio.Event | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._loop_task: asyncio.Task | None = None
        # canaries (A-5d)
        self.flush_error_counter = 0
        self.overflow_counter = 0
        self.emergency_flush_counter = 0

    # -- ledger sink (frozen signature) ----------------------------------
    def ledger_sink(
        self,
        tool_name: str,
        tool_category: str | None,
        agent_id: str,
        project_id: str,
        ok: bool,
    ) -> None:
        """NAMES-ONLY ledger sink: increment the counter map, no DB I/O.

        ``tool_category`` and ``ok`` are deliberately ignored — the ledger
        stores names + counts only (Phase 2 consumes ``ok`` from its own
        sink). ``day`` is minted HERE, at enqueue (UTC discipline, single
        point where local time could leak).
        """
        del tool_category, ok  # NAMES-ONLY
        key = (_utc_day(), project_id, tool_name, agent_id)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            self._dirty.add(key)

    def note_overflow(self) -> None:
        with self._lock:
            self.overflow_counter += 1

    def dirty_count(self) -> int:
        with self._lock:
            return len(self._dirty)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Start the respawn-on-death supervisor (main loop only).

        Safe to call from the sync ``Manager.__init__``: when invoked from
        the async lifespan a loop is running and the task is created; when
        invoked from a sync (test) context with no loop it is a no-op and a
        caller with a loop starts it explicitly.
        """
        if self._supervisor_task is not None and not self._supervisor_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[ToolUsage] no running loop at start(); deferred")
            return
        self._stop = asyncio.Event()
        self._supervisor_task = asyncio.create_task(
            self._supervise(), name="tool-usage-flusher-supervisor"
        )

    async def _supervise(self) -> None:
        while self._stop is not None and not self._stop.is_set():
            try:
                self._loop_task = asyncio.create_task(
                    self._run_loop(), name="tool-usage-flusher-loop"
                )
                await self._loop_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — respawn on death (A-5d)
                self.flush_error_counter += 1
                _throttled_warning(
                    "flusher:supervisor",
                    "[ToolUsage] flusher loop died (%s); respawning",
                    exc,
                )
                await asyncio.sleep(1.0)

    async def _run_loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            # 1 s slices keep the 30 s cadence while staying responsive to
            # stop / size / emergency pressure.
            for _ in range(max(1, int(self._interval_s))):
                if self._stop.is_set():
                    return
                dirty = self.dirty_count()
                if dirty >= self._emergency_at:
                    with self._lock:
                        self.emergency_flush_counter += 1
                    await self.flush_once()
                    break
                if dirty >= self._size_trigger:
                    await self.flush_once()
                    break
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                    return  # stop requested
                except asyncio.TimeoutError:
                    pass
            else:
                await self.flush_once()

    async def flush_once(self) -> int:
        """Snapshot dirty deltas and upsert them once; never drop on failure."""
        with self._lock:
            if not self._dirty:
                return 0
            keys = list(self._dirty)
            deltas = []
            for key in keys:
                count = self._counts.pop(key, 0)
                if count > 0:
                    from daemon.repositories.tool_usage.repository import ToolUsageDelta

                    deltas.append(
                        ToolUsageDelta(
                            day=key[0],
                            project_id=key[1],
                            tool_name=key[2],
                            agent_id=key[3],
                            call_count=count,
                            updated_at=_utc_now_iso(),
                        )
                    )
            self._dirty.clear()
        if not deltas:
            return 0
        try:
            from asyncio import to_thread

            return await to_thread(self._repo.upsert_batch, deltas)
        except Exception as exc:  # noqa: BLE001 — DB failure leaves keys dirty
            # Coalesced retry: re-add the deltas; NEVER drop a counter.
            with self._lock:
                for delta in deltas:
                    key = (
                        delta.day,
                        delta.project_id,
                        delta.tool_name,
                        delta.agent_id,
                    )
                    self._counts[key] = self._counts.get(key, 0) + delta.call_count
                    self._dirty.add(key)
            self.flush_error_counter += 1
            _throttled_warning(
                "flusher:db",
                "[ToolUsage] upsert failed (%s); counters retained for retry",
                exc,
            )
            return 0

    async def drain(self, timeout: float | None = None) -> int:
        """Bounded final drain (A-5c): stop the loop, flush all dirty keys.

        Called explicitly from the shutdown sequence BEFORE
        ``dispose_db_pools``. Never rides ``manager._background_tasks``.
        """
        bound = self._drain_bound_s if timeout is None else timeout
        deadline = time.monotonic() + bound
        if self._stop is not None:
            self._stop.set()
        to_cancel = [
            task
            for task in (self._loop_task, self._supervisor_task)
            if task is not None and not task.done()
        ]
        for task in to_cancel:
            task.cancel()
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
        flushed = 0
        while self.dirty_count() and time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                flushed += await asyncio.wait_for(
                    self.flush_once(), timeout=remaining
                )
            except asyncio.TimeoutError:
                _throttled_warning(
                    "flusher:drain",
                    "[ToolUsage] shutdown drain hit the %.1fs bound",
                    bound,
                )
                break
        return flushed


# ── The tap wrapper factory ────────────────────────────────────────────────
def wrap_tools_node(
    inner_node: Callable[..., Any],
    *,
    instance_repository: Any = None,
    flusher: ToolUsageFlusher | None = None,
) -> Callable[..., Any]:
    """Wrap a tools-node callable with the observation tap.

    The returned async node has the SAME ``(state, config: RunnableConfig)``
    signature as the inner node (LangGraph injects config by inspecting the
    annotation). It delegates to ``inner_node`` FIRST-class — the inner
    result (and any exception) passes through unchanged; all observation
    work is wrapped in swallow-all guards.
    """

    async def node(state: Any, config: Optional[RunnableConfig] = None) -> Any:
        tool_calls: list[Any] = []
        agent_id = UNKNOWN_AGENT
        project_id = NONE_PROJECT
        instance_id = ""
        # --- A-2: resolve identity ONCE per node invocation (per batch) ---
        try:
            cfg = config if isinstance(config, dict) else (config or {})
            if not isinstance(cfg, dict):
                cfg = {}
            configurable = (
                cfg.get("configurable")
                if isinstance(cfg.get("configurable"), dict)
                else {}
            )
            instance_id = (
                configurable.get("thread_id") if isinstance(configurable, dict) else None
            ) or ""

            messages = state.get("messages") if isinstance(state, dict) else None
            last_ai = None
            for message in reversed(messages or []):
                if getattr(message, "tool_calls", None):
                    last_ai = message
                    break
            tool_calls = list(getattr(last_ai, "tool_calls", None) or [])

            if instance_id and instance_repository is not None:
                inst = await asyncio.to_thread(instance_repository.get, instance_id)
                if inst is not None:
                    agent_id = getattr(inst, "agent_id", None) or UNKNOWN_AGENT
                    metadata = getattr(inst, "instance_metadata", None) or {}
                    project_id = metadata.get("project_id") or NONE_PROJECT
        except Exception:  # pragma: no cover - observation never breaks the call
            pass

        # --- the actual tool call: result / exceptions pass through ---
        result = await inner_node(state, config)

        # --- post: ok flag + choke + fan-out (all swallow-all) ---
        try:
            if tool_calls and is_collection_enabled():
                ok_by_id = _ok_map_from_result(result)
                for tc in tool_calls:
                    name = _tool_call_name(tc)
                    if not name:
                        continue
                    tc_id = _tool_call_id(tc)
                    ok = ok_by_id.get(tc_id, True) if tc_id else True
                    safe_name = _choke(name, "tool_name", flusher)
                    safe_agent = _choke(agent_id, "agent_id", flusher)
                    safe_project = _choke(project_id, "project_id", flusher)
                    category = resolve_tool_category(name)
                    for sink in _snapshot_sinks():
                        try:
                            sink(safe_name, category, safe_agent, safe_project, ok)
                        except Exception as exc:  # noqa: BLE001 - isolate sinks
                            _throttled_warning(
                                "sink:raise",
                                "[ToolUsage] a sink raised (%s); isolated",
                                exc,
                            )
        except Exception:  # pragma: no cover - observation never breaks the call
            pass
        return result

    return node


def _tool_call_name(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name") or "")
    return str(getattr(tc, "name", "") or "")


def _tool_call_id(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("id") or "")
    return str(getattr(tc, "id", "") or "")


def _ok_map_from_result(result: Any) -> dict[str, bool]:
    """Best-effort success flag per tool_call from the ToolNode result.

    ``ToolMessage.status != "error"`` ⇒ ok; default True on ambiguity.
    """
    messages: Any = None
    if isinstance(result, dict):
        messages = result.get("messages")
    elif isinstance(result, list):
        messages = result
    ok_map: dict[str, bool] = {}
    for message in messages or []:
        if getattr(message, "tool_call_id", None) is None:
            continue
        status = getattr(message, "status", None)
        ok_map[str(message.tool_call_id)] = status != "error"
    return ok_map


__all__ = [
    "ToolUsageSink",
    "ToolUsageFlusher",
    "wrap_tools_node",
    "register_sink",
    "unregister_sink",
    "is_collection_enabled",
    "resolve_tool_category",
    "COUNTING_CONTRACT",
    "COLLECTION_KEY",
    "COLLECTION_ENV_VAR",
    "NAME_CHOKE_CHARS",
    "FLUSH_INTERVAL_S",
    "FLUSH_SIZE_TRIGGER",
    "EMERGENCY_DIRTY_KEYS",
    "DRAIN_BOUND_S",
]
