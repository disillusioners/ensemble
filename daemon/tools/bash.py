"""Bash execution tool for running shell commands."""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from functools import partial
from uuid import uuid4
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from langchain_core.tools import tool
from pydantic import BaseModel

from ._tool_registry import register_tool_category
# agent-secrets P2 (design §1a) — the resolver vocabulary is IMPORTED
# (never redefined; commission hard constraint). No import cycle:
# ``secret_service`` / ``secret_resolver`` / ``kms_resolver`` do not
# reach back into ``daemon.tools``.
from daemon.services.kms_resolver import resolve_env as _kms_resolve_env
from daemon.services.secret_persistence_scrub import (
    get_process_secret_service as _get_process_secret_service,
)
from daemon.services.secret_resolver import (
    resolve_secret_markers as _resolve_secret_markers,
)
from daemon.services.secret_service import SecretMarkerResolutionError
# D4 — pid_registry stamp lane (Phase-2 resource-cost-control).
# The bash lane is the FIRST ``kind='bash'`` consumer; the chart
# and mcp lanes share the same registry. Late import via the
# ``daemon.services.*`` package boundary mirrors the kms_resolver
# pattern above (no daemon.tools → daemon.services import cycle).
from daemon.services.resource_monitor.pid_registry import (
    EXIT_KIND_CANCELLED,
    EXIT_KIND_RAM_KILLED,
    EXIT_KIND_SUCCESS,
    EXIT_KIND_TIMEOUT,
    KIND_BASH,
    KIND_CHART_RENDER,
    get_pid_registry,
)
# bash-tool orphan reaper Phase 1: durable spawn registry + env stamp.
# Late-ish import (module-level) — the reaper module has NO dependency on
# daemon.tools, so there is no import cycle. The kill-switch parse + boot
# id + env-var name are the only symbols the bash seam needs.
from daemon.services.bash_spawn_reaper import (
    BASH_TRACKING_ENV_VAR as _BASH_TRACKING_ENV_VAR,
    CURRENT_BOOT_ID as _CURRENT_BOOT_ID,
    kill_switch_from_env as _bash_reaper_switch,
    sweep_instance as _bash_reaper_sweep_instance,
)

#: FOREIGN ownership stamps (leader ruling B, 2026-11-11): the bash lane
#: strips these from the assembled env AFTER the overlay is applied, so an
#: LLM-supplied ``env`` overlay can never forge the OTHER lane's stamp.
#: Duplicated literal (the import freeze forbids ``bash → proc_tools``);
#: the coupling is pinned by a test.
_RAM_FOREIGN_OWNERSHIP_ENV_VARS: tuple[str, ...] = ("ENSEMBLE_PROC_TRACKING_ID",)
from daemon.services.timestamps import now_utc_iso as _now_utc_iso
# Spawn-boundary env scrub (rogue-containment component B, (e′)):
# strip bind/DB/attestation vars from the inherit base before handing
# env to children. Pure module; no import cycle (daemon.services only).
# Spawn-boundary env scrub (rogue-containment component B).
# FAIL-POLARITY (leader decision, pinned): an import failure here is
# FAIL-OPEN + LOUD — availability wins (a broken local policy module must
# not brick the whole bash tool), but every affected spawn logs at ERROR.
try:
    from daemon.services.spawn_env_policy import (
        apply_spawn_boundary as _apply_spawn_boundary,
    )
    _SPAWN_BOUNDARY_AVAILABLE = True
except Exception as _spawn_policy_exc:  # pragma: no cover - defensive
    _SPAWN_BOUNDARY_AVAILABLE = False
    _SPAWN_BOUNDARY_IMPORT_ERROR = _spawn_policy_exc

    def _apply_spawn_boundary(os_env, overlay=None, deliberate=None):
        return dict(os_env) if os_env else {}

# INTEGRATION (latest-absorption merge, 2026-11-12): BOTH import seams
# coexist — the rogue-containment spawn-boundary scrub above and the
# ram-auto-termination imports below. Neither side wins; both features
# are active on this lane.
# RAM auto-termination (phase3-plan.md) — the bash lane's RAM arm.
# The import direction is ``tools/* → ram_guard`` ONLY (arch §7's import
# freeze): the ``ram_guard`` package imports NOTHING from ``daemon/``.
from daemon.services.ram_guard import (
    get_ram_guard as _get_ram_guard,
    read_verdict as _read_ram_verdict,
    register as _ram_register,
    release as _ram_guard_release,
)
from daemon.services.ram_guard.errors import (
    format_ram_envelope as _format_ram_envelope,
)
from daemon.services.ram_guard.guard import (
    RAM_GUARD_PENDING_MAX as _RAM_GUARD_PENDING_MAX,
)
from daemon.services.ram_guard.verdict import (
    LANE_BASH as _RAM_LANE_BASH,
    STATE_KILLED as _RAM_STATE_KILLED,
    STATE_OVERCOUNTED as _RAM_STATE_OVERCOUNTED,
    STATE_REFUSED as _RAM_STATE_REFUSED,
    STATE_RELEASED as _RAM_STATE_RELEASED,
    STATE_WATCHING as _RAM_STATE_WATCHING,
    SpawnRecord as _RamSpawnRecord,
)

if TYPE_CHECKING:
    # Writer-injection seam (lifespan wiring): typed here only — importing
    # the repository at runtime would pull the SQLModel/engine chain into
    # every bash-tool import (and the seam must stay duck-typed for tests).
    from daemon.repositories.bash_spawn import BashSpawnRepository

logger = logging.getLogger(__name__)

# bash-tool orphan reaper minor-5: the durable spawn stamp must NOT
# queue on the loop's DEFAULT executor. During a PG outage a spawn
# burst pins default-executor threads for the full 1.5 s bounded-wait
# each (late-tombstone tasks keep the threads busy after the wait
# gives up) — starving every other to_thread user. A dedicated BOUNDED
# executor (2 workers) turns a burst into a short queue: at most 2
# inserts in flight, the rest wait their turn, and the default
# executor is never touched. On timeout the detached insert still
# lands its best-effort late tombstone from this pool.
_DURABLE_INSERT_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="bash-durable-stamp"
)

# Spawn-seam bounded wait for the durable stamp (single source: the
# ``wait_for`` below AND the late-stamp WARNING message interpolate this
# constant — no duplicated magic number).
_DURABLE_STAMP_WAIT_SECONDS = 1.5


# ── D5 — kind-hint ContextVar (resource-cost-control Phase 2) ───────────────
#
# The chart_render lane stamps its bash children with
# ``kind='chart_render'`` rather than the default ``kind='bash'`` so
# the per-instance attribution in the sampler can identify
# chromium/mmdc process trees. The hint rides a ContextVar so a
# nested invoke (e.g. a charter re-dispatch from inside a chart
# render) does not leak the wrong kind into the inner call's bash
# children.
#
# Default = KIND_BASH. The chart tool pushes ``KIND_CHART_RENDER``
# around its ``invoke_agent_and_wait`` call (a ContextVar token is
# restored in the finally block so the hint does not outlive the
# chart call).
#
# RESIDUAL ROLE (W-1, P2 review): the ContextVar hint CANNOT carry
# the chart_render kind across the production graph boundary — the
# charter's graph executes on a worker THREAD (worker_pool →
# task_processor → main_loop_bridge via run_coroutine_threadsafe),
# which copies the worker thread's FRESH context, so a hint set in
# the chart tool's asyncio context never reaches the charter's
# bash stamps. The load-bearing production path is the
# INSTANCE-KEYED map on the pid registry
# (``chart_tools`` records ``{child_instance_id → 'chart_render'}``
# after ``invoke_agent_and_wait`` returns; the bash stamp site
# consults it via :func:`_resolve_pid_kind`). The ContextVar
# remains for direct in-context callers (tests; any future caller
# that invokes bash inside the setter's own context).
_PID_KIND_HINT: ContextVar[Optional[str]] = ContextVar(
    "ens_bash_pid_kind_hint", default=None
)


def _current_pid_kind() -> str:
    """Read the ContextVar; fall back to KIND_BASH."""
    return _PID_KIND_HINT.get() or KIND_BASH


def _resolve_pid_kind(instance_id: Optional[str]) -> str:
    """Resolve the pid-registry ``kind`` for a bash stamp (W-1).

    Ladder: ContextVar hint (in-context callers) → the registry's
    instance-keyed map (production chart lane — thread-proof) →
    ``KIND_BASH``. Resolved at STAMP time against the live
    singleton so the test fixture's patched registry is honoured.
    """
    hint = _PID_KIND_HINT.get()
    if hint:
        return hint
    mapped = get_pid_registry().kind_for_instance(instance_id)
    if mapped:
        return mapped
    return KIND_BASH


# ── W-2 (P2 review) — sanitizer redaction primitives ────────────────────────
#
# ``resource_pid_map.command`` / ``.detail`` are persisted OUTSIDE
# the secrets-P2 scrub seams (the scrub covers tool ARGS + results
# + queue rows; the pid-registry stamp is a derived observability
# lane), so the sanitizer itself must neutralize the secret-bearing
# shapes that reach it:
#
#   * env-assignment tokens (``KEY=value``) — the head-of-line
#     ``OPENAI_API_KEY=sk-... curl …`` idiom puts the KEY=value
#     pair in the FIRST token, which is exactly what the old
#     string-mode ``split(None, 1)[0]`` persisted; list-mode argv
#     tokens can carry the same shape via ``env CMD …``;
#   * flag-assignment tokens (``--api-key=sk-...`` / ``-k=sk-...``)
#     — the final-gate FINDING #1 class (real-PG canary proved the
#     dash-flag value persisted verbatim);
#   * tokens CARRYING well-known secret-token material (``sk-`` /
#     ``ghp_`` / ``xox[baprs]-`` / ``AKIA`` / ``glpat-``) regardless
#     of position in the token;
#   * ensemble secret markers (``__ENS_SECRET__<uuid4>__``, FROZEN
#     shape — daemon/services/secret_service.py) and KMS-Lite
#     markers (``__KMS_REF__…__`` / ``__KMS_ENV__…__``) riding
#     inside any token.
#
# The KEY=/FLAG= prefix is KEPT on redacted assignments (forensic
# correlation without material — same discipline as the
# env_keys-only detail).
_ENV_ASSIGN_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# W-2 (final-gate FINDING #1): flag-assignment argv tokens —
# ``--api-key=sk-...`` / ``-k=sk-...`` (any dash-prefixed flag with
# an ``=``-attached value) are exactly as secret-bearing as the
# ``KEY=value`` shape, and they PERSIST verbatim otherwise (the
# real-PG canary proved ``--api-key=sk-…`` survives into
# ``resource_pid_map.command``). The flag NAME is kept (forensic
# correlation — same discipline as KEY=); only the RHS is dropped.
# Requires a non-empty value (``--flag=`` with an empty RHS carries
# nothing and passes through).
_FLAG_ASSIGN_TOKEN_RE = re.compile(r"^(--?[A-Za-z0-9_.-]+)=(.+)$")
# W-2 (final-gate FINDING #1, second shape): a STANDALONE token that
# CARRIES well-known secret-token material regardless of shape — the
# literal secret itself as an argv token, or riding mid-token. The
# prefix families are the canary classes the final gate pinned
# (sk- / ghp_ / xox[baprs]- / AKIA / glpat-); ENS/KMS markers keep
# their own dedicated machinery above (startswith exact families +
# the joined-string sweep). The lookbehind requires the prefix to
# start at a non-alphanumeric boundary so benign words that merely
# CONTAIN the letters (``task-123``, ``risk-a``, ``desk-1``) do NOT
# trip it — ``sk-`` after ``=``/``:``/``/``/start-of-token does.
_SECRET_VALUE_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-|ghp_|xox[baprs]-|AKIA|glpat-)"
)
_SECRET_MARKER_TOKEN_PREFIXES = (
    "__ENS_SECRET__",
    "__KMS_REF__",
    "__KMS_ENV__",
)
# Belt: sweep well-formed ENS markers out of any JOINED string
# (covers markers embedded mid-token in string-mode argv0).
_SECRET_MARKER_SEARCH_RE = re.compile(
    r"__ENS_SECRET__[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
    r"-[0-9a-f]{4}-[0-9a-f]{12}__"
)


def _redact_registry_token(token: str) -> str:
    """Redact one argv token for the pid-registry stamp (W-2).

    Marker-carrying tokens → ``<redacted>`` entirely; env-assignment
    tokens → ``KEY=<redacted>`` (KEY kept, value dropped);
    flag-assignment tokens (``--api-key=sk-...`` / ``-k=...``) →
    ``FLAG=<redacted>`` (flag kept, value dropped); tokens CARRYING
    known secret-token material (``sk-`` / ``ghp_`` / ``xox[baprs]-``
    / ``AKIA`` / ``glpat-`` at a token boundary, anywhere in the
    token) → ``<redacted>`` entirely; anything else passes through
    unchanged.

    Deliberate residuals (documented, not accidental):

      * ``--flag <value>`` SPACE-separated form: the value token has
        NO assignment shape and (unless it itself carries a secret
        prefix) no known-secret marker — a bare ``abc123`` is
        indistinguishable from any positional argument, and
        redacting every non-flag token would destroy the
        fingerprint's forensic utility. Callers must pass secrets
        as ``--flag=<value>`` (assignment shape) or via env/KMS
        markers.
      * Unknown/custom secret formats that match none of the pinned
        prefix families pass through unless assignment-shaped.
    """
    if token.startswith(_SECRET_MARKER_TOKEN_PREFIXES):
        return "<redacted>"
    m = _ENV_ASSIGN_TOKEN_RE.match(token)
    if m:
        return f"{token[: m.end()]}<redacted>"
    m = _FLAG_ASSIGN_TOKEN_RE.match(token)
    if m:
        # group(1) end — NOT m.end() (the trailing (.+) makes the
        # whole-match end the END OF STRING, which would keep the
        # secret in the persisted token).
        return f"{token[: m.end(1)]}=<redacted>"
    if _SECRET_VALUE_PREFIX_RE.search(token):
        return "<redacted>"
    return token


def _strip_secret_markers(text: str) -> str:
    """Belt-and-braces marker sweep over a joined persisted string."""
    return _SECRET_MARKER_SEARCH_RE.sub("<redacted>", text)


def _sanitize_command_for_registry(
    command: "str | List[str]",
    env: Optional[Dict[str, str]],
) -> "tuple[str, str]":
    """Build a D14-safe ``(command, detail)`` pair for the pid_registry stamp.

    D14 alignment (secrets): ``resource_pid_map.command`` MUST NEVER
    carry secret-bearing text — markers, plaintext keys, raw env
    values. The sanitizer returns the argv-shape ONLY, with W-2
    redaction applied:

      * list command → ``"<argv0> <argv1> …"`` truncated to a small
        budget (full argv is recoverable from the agent log lane;
        the registry just needs the fingerprint). Each token passes
        :func:`_redact_registry_token` — env-assignment-shaped argv
        tokens (``env KEY=value CMD`` idiom) persist as
        ``KEY=<redacted>``, flag-assignment tokens
        (``--api-key=sk-...``) persist as ``FLAG=<redacted>``,
        tokens carrying known secret-prefixed material persist as
        ``<redacted>``, and marker-carrying tokens persist as
        ``<redacted>`` (argv IS a tool-call arg — a secret in an
        argv token would otherwise land in resource_pid_map
        outside the secrets-P2 scrub seams).
      * string command → the first NON-env-assignment token —
        leading ``KEY=value …`` prefixes are stripped BEFORE taking
        argv[0] (the old ``split(None, 1)[0]`` persisted the
        head-of-line ``OPENAI_API_KEY=sk-...`` pair itself — the
        exact class the docstring excluded). The retained argv0
        still passes :func:`_redact_registry_token`, so a
        head-of-line ``--api-key=sk-...`` flag token persists as
        ``--api-key=<redacted>`` (flag kept, value dropped) and a
        secret-prefixed argv0 persists as ``<redacted>``. An
        all-assignments string falls back to the REDACTED first
        token so the row still carries a fingerprint.

    The ``detail`` column carries the full shape (length, argv
    count, env overlay KEYS only — never values) so forensics can
    correlate without leaking material; the embedded ``argv0=``
    echo is the SANITIZED token (W-2: the old shape echoed the raw
    head-of-line KEY=value pair into ``detail``).

    Args:
        command: The bash input (str or list[str]).
        env: The env overlay (keys-only kept; values dropped).

    Returns:
        ``(command, detail)`` — both safe to persist.
    """
    cmd_budget = 256
    if isinstance(command, list):
        argv: Sequence[str] = [_redact_registry_token(str(t)) for t in command]
        cmd_str = " ".join(argv[:8])
        if len(cmd_str) > cmd_budget:
            cmd_str = cmd_str[: cmd_budget - 3] + "..."
        shape_detail = f"list argv={len(command)}"
    else:
        tokens = command.split() if command else []
        body = tokens
        while body and _ENV_ASSIGN_TOKEN_RE.match(body[0]):
            body = body[1:]
        first_raw = (
            body[0] if body
            else (tokens[0] if tokens else "")
        )
        first_token = _redact_registry_token(first_raw)
        cmd_str = first_token[:cmd_budget]
        shape_detail = (
            f"string len={len(command)} argv0={first_token[:64]!r}"
        )
    # Env overlay: KEYS ONLY — values would carry markers/plaintext.
    # Truncate the key list to 16 entries so a 100-key env never
    # bloats the detail column.
    if env:
        keys = sorted(env.keys())
        env_part = f" env_keys={','.join(keys[:16])}"
        if len(keys) > 16:
            env_part += f" (+{len(keys) - 16} more)"
    else:
        env_part = ""
    # Final belt: no well-formed ENS marker survives into either
    # persisted string (covers mid-token markers in string mode).
    return (
        _strip_secret_markers(cmd_str),
        _strip_secret_markers(shape_detail + env_part),
    )


def _read_proc_start_time(pid: int) -> Optional[int]:
    """Read ``/proc/<pid>/stat`` field 22 (starttime jiffies) on Linux.

    Best-effort. Returns ``None`` on any failure — the registry
    accepts a None start_time (registration succeeds; the
    PID-recycle check at lookup time simply cannot verify a None
    value and falls through to the pgid fallback).

    Non-Linux platforms always return ``None`` (the registry is
    process-lifetime and platform-agnostic — the per-row
    start_time column is simply NULL on macOS/Windows).
    """
    if sys.platform == "win32":
        return None
    try:
        with open(f"/proc/{pid}/stat", "r") as h:
            line = h.readline()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    # ``(comm)`` may contain spaces/parens — split on the LAST ``)``.
    last_paren = line.rfind(")")
    if last_paren == -1:
        return None
    _, _, tail = line.rpartition(")")
    parts = tail.strip().split()
    # field 22 = parts[19] (offset accounts for the stripped ``(comm)``)
    try:
        return int(parts[19])
    except (ValueError, IndexError):
        return None


def _read_proc_vm_hwm_kb(pid: int) -> Optional[int]:
    """Read ``/proc/<pid>/status`` VmHWM (kB) on Linux, best-effort.

    Returns ``None`` on any failure (process-gone, missing file,
    parse error). The caller passes the result into
    ``PidRegistry.mark_exit(peak_rss_kb=...)`` — the registry does
    NOT re-validate; a None peak just means we raced and missed
    the reap window.
    """
    if sys.platform == "win32":
        return None
    try:
        with open(f"/proc/{pid}/status", "r") as h:
            body = h.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    for line in body.splitlines():
        if line.startswith("VmHWM:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None


def _pid_registry_register(
    *,
    pid: int,
    start_time: Optional[int],
    kind: str,
    instance_id: str,
    pgid: int,
    command: str,
    detail: str,
    parent_pid: int,
) -> None:
    """Slim shim around :meth:`PidRegistry.register` for the bash lane.

    Hides the start_time-None edge case (Linux-only call site —
    the registry accepts None and the lookup just falls through to
    pgid fallback).
    """
    get_pid_registry().register(
        pid=pid,
        start_time=start_time if start_time is not None else 0,
        kind=kind,
        instance_id=instance_id,
        pgid=pgid,
        command=command,
        detail=detail,
        parent_pid=parent_pid,
    )


def _pid_registry_mark_exit(
    *,
    pid: int,
    start_time: Optional[int],
    exit_code: Optional[int],
    exit_kind: str,
    peak_rss_kb: Optional[int],
) -> None:
    """Slim shim around :meth:`PidRegistry.mark_exit` for the bash lane.

    Best-effort: if the start_time is None (the eager /proc read
    raced and missed) we still call mark_exit — the registry
    matches by ``pid`` AND ``start_time``; a None stored against a
    0 sentinel will refuse any real start_time lookup later, so
    we MUST pass through whatever the bash lane captured.

    Returns None on refusal (idempotent — timeout arm + cancellation
    arm + success arm may all race against a fast exit).
    """
    get_pid_registry().mark_exit(
        pid=pid,
        start_time=start_time if start_time is not None else 0,
        exit_code=exit_code,
        exit_kind=exit_kind,
        peak_rss_kb=peak_rss_kb,
    )


@dataclass
class BashProcessEntry:
    """A bash subprocess whose PID and PGID were captured at spawn."""

    pid: int
    pgid: int


class BashProcessRegistry:
    """Track bash-spawned subprocess groups by owning instance ID."""

    def __init__(self) -> None:
        self._entries: Dict[str, List[BashProcessEntry]] = {}
        self._lock = asyncio.Lock()
        # bash-tool orphan reaper Phase 1: durable writer (injected from
        # the lifespan via set_durable_writer). None ⇒ no row written
        # (writer-unset ordering contract, plan D3 adjudication 2).
        self._durable_writer: "BashSpawnRepository | None" = None

    def set_durable_writer(self, writer: "BashSpawnRepository | None") -> None:
        """Inject the durable spawn-registry repository (lifespan wiring)."""
        self._durable_writer = writer

    def get_durable_writer(self) -> "BashSpawnRepository | None":
        """Return the injected durable writer (or None)."""
        return self._durable_writer

    async def register(self, instance_id: str, pid: int, pgid: int) -> None:
        """Register a bash subprocess under its owning instance."""
        async with self._lock:
            self._entries.setdefault(instance_id, []).append(
                BashProcessEntry(pid=pid, pgid=pgid)
            )

    async def unregister(self, instance_id: str, pid: int) -> None:
        """Remove one subprocess after an explicit ``_kill_process`` call."""
        async with self._lock:
            entries = self._entries.get(instance_id)
            if not entries:
                return
            self._entries[instance_id] = [
                entry for entry in entries if entry.pid != pid
            ]
            if not self._entries[instance_id]:
                del self._entries[instance_id]

    def tracked_count(self) -> int:
        """Total tracked subprocess rows across all instances (probe).

        N-1: the zero-monotonic-growth test asserts BOTH registries
        return to zero after successful bash calls — this is the
        cheap counter for the old registry (the pid_registry twin
        is :meth:`PidRegistry.live_count`).
        """
        return sum(len(v) for v in self._entries.values())

    async def cleanup_instance(
        self, instance_id: str, *, reaped_by: str = "terminal-tier"
    ) -> int:
        """Kill all tracked process groups for one instance.

        Two tiers (bash-tool orphan reaper Phase 2, plan D4):

        1. **RAM tier (unchanged, runs FIRST):** killpg every tracked
           group bucket, then forget them.
        2. **Durable tier (strictly additive, AFTER the RAM tier):**
           consult the OPEN rows of the durable spawn registry
           (``bash_spawn_registry``) for this instance — dedup'd by
           pgid against the RAM tier, so a group the RAM tier just
           SIGKILLed is closed without a second signal — verify each
           row (hard guards + D8 sibling guard + D9 start-time gate +
           env attribution) and reap the verified group via the
           TERM→grace→KILL ladder. Master switch OFF ⇒ complete no-op
           with ZERO repository access (m3); writer-unset (unit tests
           / pre-lifespan calls) ⇒ no-op (D3 adjudication 2).

        ``reaped_by`` records the cleanup-point lane in the closed
        rows (``terminal-tier`` default — terminal Tier-1/Tier-2,
        terminate, chart all inherit this reach with ZERO call-site
        changes; ``shutdown`` for the shutdown lane).
        """
        async with self._lock:
            entries = self._entries.pop(instance_id, [])

        killed = 0
        for entry in entries:
            try:
                self._kill_group(entry.pgid)
                killed += 1
            except Exception as e:
                logger.warning(
                    f"bash cleanup: killpg({entry.pgid}) failed: "
                    f"{type(e).__name__}: {e}"
                )

        # bash-tool orphan reaper Phase 2 (D4): durable-row reach —
        # strictly additive AFTER the RAM killpg pass, dedup'd by pgid.
        # Best-effort: a sweep failure must never break the caller.
        # minor-6: the durable sweep's closed count is NOT an all-kills
        # figure (it includes gc / refused / dedup closures), so it is
        # logged SEPARATELY with a pointer to the per-outcome census
        # instead of being folded into one inflated "killed" number.
        durable_closed = 0
        try:
            durable_closed = await _bash_reaper_sweep_instance(
                self._durable_writer,
                instance_id,
                reaped_by=reaped_by,
                ram_pgids={entry.pgid for entry in entries},
            )
            if durable_closed:
                logger.info(
                    "bash cleanup: durable-row sweep closed %d row(s) for "
                    "%s (killed/gc/refused breakdown in the reaper sweep "
                    "census)",
                    durable_closed, instance_id[:8],
                )
        except Exception as e:
            logger.warning(
                f"bash cleanup: durable-row sweep failed for "
                f"{instance_id[:8]}: {type(e).__name__}: {e}"
            )
        # Int return contract preserved: total lanes-closed actions.
        return killed + durable_closed

    async def cleanup_all(self, *, reaped_by: str = "shutdown") -> int:
        """Kill every tracked bash process group during daemon shutdown.

        Per tracked instance this runs the same two tiers as
        :meth:`cleanup_instance` (RAM killpg first, then the durable
        row consult). Instances whose RAM buckets are already empty
        (Path A stragglers whose success arm unregistered them) are
        reached by the manager's shutdown durable sweep
        (``sweep_open_rows_for_shutdown``), not here — this method
        only enumerates the instances it still tracks.

        Known limitations (bash-tool orphan reaper status, plan §2
        tasks 2-4): the setsid-escapee gap and the crash-recovery gap
        are closed by the reaper phases 2-4 — every bash-tool spawn
        now leaves a durable tombstone row (written at the spawn
        seam), so setsid escapees are reached by the Phase-4
        enumerated stamp sweep and crash/cross-boot stragglers by the
        shutdown durable sweep + the Phase-3 one-shot boot reconcile.
        The **Path E residual stays**: spawns with
        ``instance_id=None`` are untracked by design (reachability is
        direct-coroutine callers only; production wiring always
        injects an instance_id).
        """
        async with self._lock:
            instance_ids = list(self._entries.keys())

        total = 0
        for iid in instance_ids:
            try:
                total += await self.cleanup_instance(iid, reaped_by=reaped_by)
            except Exception as e:
                logger.warning(
                    f"bash cleanup_all: failed for {iid[:8]}: "
                    f"{type(e).__name__}: {e}"
                )
        if total:
            logger.info(f"bash cleanup_all: killed {total} process(es)")
        return total

    @staticmethod
    def _kill_group(pgid: int) -> None:
        """SIGKILL a process group, with a Windows task-tree fallback."""
        if sys.platform != "win32":
            os.killpg(pgid, signal.SIGKILL)
        else:
            import subprocess

            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pgid)],
                capture_output=True,
            )


_bash_process_registry: "BashProcessRegistry | None" = None


def get_bash_process_registry() -> BashProcessRegistry:
    """Return the process-wide bash process registry singleton."""
    global _bash_process_registry
    if _bash_process_registry is None:
        _bash_process_registry = BashProcessRegistry()
    return _bash_process_registry


# ── agent-secrets P2 (design §1a) — spawn-time secret resolution ──────
#
# The bash ``env`` overlay (``BashInputSchema.env``) is resolved at
# the SPAWN SITE (design §1a literal chain: assembled →
# ``resolve_secret_markers`` → ``resolve_env`` (KMS-Lite) → spawn).
# This chain is the SOLE bash.env resolution lane (review-r1
# advisory A): the wrapped_tools_node pre-invoke pass does NOT cover
# it — the ``env`` field is dict-valued and the pre-invoke walker
# resolves STRING-typed declared fields only, so the former
# ``SECRET_TYPED_ARGS["bash"]`` registration was a structural no-op
# (now removed from ``daemon/tools/_auth.py``). The chain
# short-circuits on marker-free values (resolver §0 fast path —
# zero measurable latency) and FAILS CLOSED on a marker it cannot
# resolve: the child is NOT spawned rather than a literal
# ``__ENS_SECRET__…__`` leaking into a subprocess env (any caller —
# wrapper or direct coroutine invocation — gets the same guarantee).
#


def _format_secret_envelope(exc) -> str:
    """Format a :class:`SecretMarkerResolutionError` as the typed
    JSON envelope (design §3.3 vocabulary — ``surface="bash.env"``).

    PB-F1: the envelope carries code + surface + the anti-
    differential message; the ``secret_id`` rides the exception's
    attribute (operator diagnostics) but NEVER the agent-visible
    payload.
    """
    return json.dumps(
        {
            "ok": False,
            "code": getattr(exc, "code", "secret_resolver_store_unavailable"),
            "surface": getattr(exc, "surface", "bash.env"),
            "message": str(exc),
        }
    )

CATEGORY_NAME = "Shell"
CATEGORY_DOC = """\
Execute shell commands.

**Rules**:
- Always set `workdir` to the project directory. Never omit it.
- If you need to access files outside the project directory, do so by passing
  explicit absolute paths inside the command (e.g. `/abs/path/to/file`) — the
  `workdir` setting still applies as the command's `cwd`.
- Avoid blocking commands (e.g., `tail -f`, `watch`, interactive editors) — except via skills that use CLI.
- For large output, redirect to file and read with `read_file`:
  ```
  command > /tmp/output.txt && echo "Done"
  ```
"""


async def _kill_process(
    proc: asyncio.subprocess.Process, pgid: int | None = None
) -> None:
    """Gracefully kill a process: SIGTERM, wait 5s, then SIGKILL.

    On Unix, kills the entire process group so backgrounded children
    (e.g., `nohup ... &`) are terminated too. On Windows, falls back
    to killing the immediate process.

    ``pgid`` is an optional override: callers that have already captured
    the process group ID at spawn time SHOULD pass it to avoid a TOCTOU
    race against PID recycling (the in-scope ``os.getpgid(proc.pid)`` could
    race with the OS reusing a dead PID and return the wrong PGID). When
    ``pgid`` is None we fall back to ``os.getpgid(proc.pid)`` for backward
    compatibility with any callers that do not capture it.
    """
    is_unix = sys.platform != "win32"
    # Resolve target PGID once at the top — never re-derive inside the
    # SIGTERM/SIGKILL branches so we cannot accidentally kill a
    # PID-recycled group if the process exited between calls.
    target_pgid: int | None = None
    if is_unix:
        target_pgid = pgid if pgid is not None else os.getpgid(proc.pid)
    try:
        if is_unix:
            # ``target_pgid`` is int on Unix (set above); the None-branch
            # cannot be reached here, but the type checker wants a
            # narrowing for the ``killpg`` signature.
            assert target_pgid is not None
            try:
                os.killpg(target_pgid, signal.SIGTERM)
            except OSError:
                pass
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if is_unix:
                assert target_pgid is not None
                try:
                    os.killpg(target_pgid, signal.SIGKILL)
                except OSError:
                    pass
            else:
                proc.kill()
            await proc.wait()
    except OSError:
        pass


def _read_file_bytes(path):
    """Read file contents as bytes; return b'' if path is None or read fails."""
    if path is None:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


# ── RAM auto-termination — the bash lane's RAM arm (phase3-plan.md) ─────────
#
# The guard is installed at the composition root (the FastAPI lifespan —
# PHASE 5 owns that wiring).  Until then ``get_ram_guard()`` returns
# ``None`` and EVERY call below is a NO-OP: the lane behaves exactly as
# it did before (arch §4.6 item 6 — the OFF posture).  The master switch
# ``ENSEMBLE_RAM_GUARD`` (arch §15 B8) makes OFF a complete no-op even
# once a guard IS installed: no registration, no ``/proc`` reads, no
# thread, byte-identical output.

#: The master switch (arch §15 B8).  Default ON when absent; explicit
#: ``0/false/no/off`` (case-insensitive) = OFF everywhere.  The parsing
#: semantics mirror ``daemon/services/bash_spawn_reaper.py``'s
#: ``kill_switch_from_env`` (absent/empty ⇒ ON; else the value must not
#: be one of the OFF values).
RAM_GUARD_ENV_SWITCH = "ENSEMBLE_RAM_GUARD"

#: The OFF values (case-insensitive) — arch §15 B8, verbatim.
RAM_GUARD_SWITCH_OFF_VALUES = frozenset({"0", "false", "no", "off"})

#: The bounded wait's poll interval (arch §4.3.1 item 2) — 25 ms.
RAM_GUARD_WAIT_POLL_SECONDS = 0.025

#: The wait's slack (arch §15 B20, verbatim): one poll interval
#: (200 ms — the tick could hand off just after a sample) + 50 ms
#: scheduling/jitter margin.
RAM_GUARD_WAIT_SLACK_MS = 250

#: The service-worst-case components (arch §15 B20):
#: ``T_service_worst_ms = PSS_CONFIRM_DEADLINE_MS + GRACE_MS + PROBE_MS``.
#: These mirror the guard's DEFAULT config (``RamGuardConfig``); the
#: installed guard's own values widen the deadline (see
#: :func:`_ram_wait_deadline_ms`).
RAM_GUARD_PSS_CONFIRM_DEADLINE_MS = 250
RAM_GUARD_GRACE_MS = 250
RAM_GUARD_PROBE_MS = 29

#: The bounded wait's deadline (arch §15 B20, re-derived at the
#: code-council pass — §18.4, PROBE_MS DECLARED at the council-measured
#: 29 ms; the 10 ms figure was an estimate):
#: ``T_service_worst_ms = PSS_CONFIRM_DEADLINE_MS + GRACE_MS + PROBE_MS
#:                       = 250 + 250 + 29 = 529``
#: ``wait_deadline_ms = 2 × T_service_worst_ms + WAIT_SLACK_MS
#:                   = 2 × 529 + 250 = 1308``.
RAM_GUARD_WAIT_DEADLINE_MS = (
    _RAM_GUARD_PENDING_MAX
    * (
        RAM_GUARD_PSS_CONFIRM_DEADLINE_MS
        + RAM_GUARD_GRACE_MS
        + RAM_GUARD_PROBE_MS
    )
    + RAM_GUARD_WAIT_SLACK_MS
)

#: The envelope's ``code`` for the TIMEOUT-wins coincidence (arch §17.5 /
#: test-plan O7).  The code follows the claim token: the timeout that
#: won the race reports ``timeout``, the guard reports
#: ``ram_limit_exceeded`` (the substrate's ``RAM_LIMIT_EXCEEDED_CODE``).
RAM_CODE_TIMEOUT = "timeout"

#: The additive refusal line (arch §15 B25, verbatim) — the reason is
#: interpolated; the line itself is pinned.
RAM_REFUSAL_LINE = "ram_kill_refused: {reason}"

#: The additive breach line (arch §15 B25, verbatim).
RAM_BREACH_LINE = "ram_breach_observed: true"

#: The additive peak line — the wait-expiry / breach surface carries the
#: observed peak beside ``ram_breach_observed`` (arch §17.2).  The name
#: mirrors the envelope's ``observed_peak_bytes`` key.
RAM_PEAK_LINE = "ram_observed_peak_bytes: {peak_bytes}"


def _ram_guard_switch(environ: dict | None = None) -> bool:
    """Resolve the master switch — default ON, explicit OFF values off.

    The semantics mirror ``bash_spawn_reaper.kill_switch_from_env``:
    absent ⇒ ON; empty/whitespace ⇒ ON; ``0/false/no/off`` (case
    insensitive) ⇒ OFF; anything else ⇒ ON.
    """
    env = os.environ if environ is None else environ
    val = env.get(RAM_GUARD_ENV_SWITCH)
    if val is None:
        return True
    raw = val.strip().lower()
    if not raw:
        return True
    return raw not in RAM_GUARD_SWITCH_OFF_VALUES


def _ram_guard_active() -> bool:
    """Is the RAM arm live?  (the switch ON AND a guard installed)."""
    if not _ram_guard_switch():
        return False
    return _get_ram_guard() is not None


def _ram_wait_deadline_ms(guard) -> int:
    """The bounded wait's deadline for the INSTALLED guard.

    The derivation (arch §15 B20) is pinned against the documented
    defaults (1308 ms).  The guard's OWN service-worst-case inputs are
    read through its PUBLIC read-only accessors (``grace_seconds`` /
    ``pss_confirm_deadline_ms``) — NEVER its private attributes, so a
    substrate rename cannot silently re-derive the deadline.  When a
    guard does not expose them, the documented defaults are the
    fallback.
    """
    grace_ms = RAM_GUARD_GRACE_MS
    confirm_ms = RAM_GUARD_PSS_CONFIRM_DEADLINE_MS
    raw_grace = getattr(guard, "grace_seconds", None)
    if isinstance(raw_grace, (int, float)):
        grace_ms = int(round(float(raw_grace) * 1000))
    raw_confirm = getattr(guard, "pss_confirm_deadline_ms", None)
    if isinstance(raw_confirm, (int, float)):
        confirm_ms = int(raw_confirm)
    adaptive = (
        _RAM_GUARD_PENDING_MAX
        * (confirm_ms + grace_ms + RAM_GUARD_PROBE_MS)
        + RAM_GUARD_WAIT_SLACK_MS
    )
    return max(RAM_GUARD_WAIT_DEADLINE_MS, adaptive)


def _ram_register_spawn(
    *,
    tracking_id: str | None,
    instance_id: str | None,
    pid: int,
    pgid: int,
    start_time_jiffies: int | None,
    ceiling_bytes: int | None,
) -> "_RamSpawnRecord | None":
    """S1 — register the spawn with the RAM guard.

    The ALREADY-CAPTURED ``pgid`` is used — NEVER a second
    ``os.getpgid`` (the TOCTOU the capture block already respects).  The
    ceiling is the guard's RESOLVED ceiling unless an INTERNAL override
    is passed (the per-call test seam — the LLM-facing schema has NO
    ceiling parameter; the SECURITY RULING in phase3-plan.md T1).
    """
    if not _ram_guard_active():
        return None
    if not tracking_id or not instance_id:
        return None
    try:
        return _ram_register(
            tracking_id=tracking_id,
            instance_id=instance_id,
            lane=_RAM_LANE_BASH,
            pid=pid,
            pgid=pgid,
            start_time_jiffies=start_time_jiffies,
            ceiling_bytes=ceiling_bytes,
        )
    except Exception as exc:
        # Fail-open: a registration failure must NOT break the bash lane.
        logger.warning(
            "bash: ram-guard registration failed (fail-open): %s: %s",
            type(exc).__name__, exc,
        )
        return None


def _ram_episode_resolved(record: "_RamSpawnRecord | None") -> bool:
    """Is the record's episode RESOLVED (no further wait)?

    ``KILLED`` / ``REFUSED`` / ``RELEASED`` are terminal.  An
    ``OVERCOUNTED`` re-arm is resolved too — the re-armed threshold IS
    the episode's recorded outcome and no verdict is published (arch
    §17.5).  A ``WATCHING`` record inside its transient-refusal backoff
    (``transient_backoff > 0``) re-armed from a TRANSIENT refusal
    (arch §17.2 / §15 B27 / §18.5).
    """
    if record is None:
        return True
    state = record.state
    if state in (_RAM_STATE_KILLED, _RAM_STATE_REFUSED, _RAM_STATE_RELEASED):
        return True
    if state == _RAM_STATE_OVERCOUNTED:
        return True
    if state == _RAM_STATE_WATCHING and getattr(record, "transient_backoff", 0) > 0:
        return True
    return False


async def _await_ram_verdict(tracking_id: str | None) -> "_RamSpawnRecord | None":
    """S2 — read the guard's verdict, waiting BOUNDED for a terminal state.

    arch §4.3.1 item 2 / §15 B20: a 25 ms ASYNC poll (the event loop is
    never blocked), a deadline of ``RAM_GUARD_WAIT_DEADLINE_MS``
    (1308 ms).  Only an IN-FLIGHT episode is waited on — a record with
    no observed breach costs NOTHING (no deadline burn without an
    outcome).  On expiry the record's ACTUAL state is returned: a
    pending decision is reported as pending, never fabricated.
    """
    if not tracking_id:
        return None
    guard = _get_ram_guard()
    if guard is None:
        return None
    record = _read_ram_verdict(tracking_id)
    if record is None:
        return None
    if not record.breach_at:
        # No breach observed — nothing to wait for.
        return record
    if _ram_episode_resolved(record):
        return record
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (_ram_wait_deadline_ms(guard) / 1000.0)
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return record
        await asyncio.sleep(min(RAM_GUARD_WAIT_POLL_SECONDS, remaining))
        record = _read_ram_verdict(tracking_id)
        if record is None:
            return None
        if _ram_episode_resolved(record):
            return record


def _ram_claim_held(record: "_RamSpawnRecord | None") -> bool:
    """Does the guard hold the episode's claim token right now?

    The token is EPISODE-scoped (arch §17.5): the first claimant owns
    the verdict and a loser SKIPS its signal — that is what keeps the
    timeout arm and the guard's ladder from BOTH signalling.
    """
    return bool(record is not None and record.claim_token is not None)


def _ram_is_kill(record: "_RamSpawnRecord | None") -> bool:
    """Did the GUARD kill the tree?

    The VERDICT RECORD is the only discriminator — the exit code is
    ambiguous by construction (``-9`` is either cause, arch §5).
    """
    return bool(
        record is not None
        and record.reason
        and record.reason.startswith("killed:")
    )


def _ram_is_refusal(record: "_RamSpawnRecord | None") -> bool:
    """Did the episode resolve as a REFUSAL?  (arch §15 B25/B27)"""
    return bool(
        record is not None
        and record.reason
        and record.reason.startswith("refused:")
    )


def _ram_breach_observed(record: "_RamSpawnRecord | None") -> bool:
    """Was a breach OBSERVED (independent of the outcome)?"""
    return bool(record is not None and record.breach_at is not None)


def _ram_lane_release(tracking_id: str | None) -> bool:
    """The lane's finally-shaped release — CONDITIONAL on the watch.

    arch §4.3.1 item 4 / §15 B21: a lane exit is a NOTIFICATION, not
    the end of the watch.  The guard's own release API refuses a
    CLAIMED record (its release is deferred to the lane) and refuses a
    record whose GROUP is still live (the liveness sweep owns that
    release) — the lane simply ASKS.  A record that was NEVER sampled
    is left alone too: its group membership is UNKNOWN, and releasing
    it could drop a live watch (the ``cmd &`` shape).
    """
    if not tracking_id:
        return False
    try:
        record = _read_ram_verdict(tracking_id)
        if (
            record is not None
            and not record.is_terminal
            and record.samples <= 0
        ):
            return False
        return _ram_guard_release(tracking_id)
    except Exception as exc:
        logger.warning(
            "bash: ram-guard release failed (best-effort): %s: %s",
            type(exc).__name__, exc,
        )
        return False


def _ram_release_class(released: bool, record: "_RamSpawnRecord | None") -> str:
    """The release-outcome CLASS — a CONTENT-FREE token.

    The class only: never the argv, never an env value, never a secret,
    never an exception repr.  The classes are ``released`` (the guard
    dropped the entry) · ``already-terminal`` (the guard's own lane
    released it) · ``claimed`` (the record's release is deferred to
    the lane) · ``live-watch`` (a group member is still up) · ``absent``
    (no record) · ``unknown``.
    """
    if released:
        return "released"
    if record is None:
        return "absent"
    if record.is_terminal:
        return "already-terminal"
    if record.claim_token is not None:
        return "claimed"
    if record.group_member_count > 0:
        return "live-watch"
    return "unknown"


async def _ram_release_sequence(
    *,
    tracking_id: str | None,
    instance_id: str | None,
    pid: int,
    start_time_jiffies: int | None,
    exit_code: int | None,
    peak_bytes: int,
) -> None:
    """T12 — the three-registry release, IN ORDER, each best-effort.

    guard registry → ``BashProcessRegistry`` → ``pid_registry.mark_exit``.
    The whole sequence runs AFTER the verdict publication (T5) and no
    step can abort another: a raising step is logged and the sequence
    CONTINUES.  The peak is converted bytes → KB EXACTLY ONCE, here
    (T10) — the guard's peak is bytes, the registry's column is KB.
    """
    # 1. the guard registry (the guard's own bookkeeping).
    if tracking_id:
        try:
            record = _read_ram_verdict(tracking_id)
            released = bool(_ram_guard_release(tracking_id))
            # The outcome is LOGGED — content-free (the tracking id +
            # the reason class only; never argv, never an env value,
            # never a secret).
            logger.info(
                "bash: ram-guard release: tracking=%s class=%s",
                tracking_id, _ram_release_class(released, record),
            )
        except Exception as exc:
            logger.warning(
                "bash: ram-guard release failed on the RAM arm "
                "(best-effort): %s: %s", type(exc).__name__, exc,
            )
    # 2. the BashProcessRegistry (the killpg-tracking map).
    if instance_id is not None:
        try:
            await get_bash_process_registry().unregister(instance_id, pid)
        except Exception as exc:
            logger.warning(
                "bash: BashProcessRegistry.unregister failed on the RAM arm "
                "(best-effort): %s: %s", type(exc).__name__, exc,
            )
    # 3. the pid_registry — the exit kind + the ONE bytes→KB conversion.
    try:
        _pid_registry_mark_exit(
            pid=pid,
            start_time=start_time_jiffies,
            exit_code=exit_code,
            exit_kind=EXIT_KIND_RAM_KILLED,
            peak_rss_kb=round(int(peak_bytes) / 1024),
        )
    except Exception as exc:
        logger.warning(
            "bash: pid_registry.mark_exit(ram_killed) failed "
            "(best-effort): %s: %s", type(exc).__name__, exc,
        )


def _ram_additive_surface(
    content: str,
    record: "_RamSpawnRecord | None",
) -> str:
    """The additive RAM surfaces (arch §15 B25, verbatim).

    A refusal ⇒ ``ram_kill_refused: <reason>`` + ``ram_breach_observed:
    true``; an observed breach whose episode did not kill (the
    wait-expiry / over-count surface) ⇒ ``ram_breach_observed: true`` +
    the observed peak.  The lines are ADDITIVE — the command's output
    is never swallowed — and the surface is distinct from
    ``code="ram_limit_exceeded"`` by construction (it never uses it).
    """
    if _ram_is_refusal(record):
        return (
            content
            + "\n\n"
            + RAM_REFUSAL_LINE.format(reason=record.reason)
            + "\n"
            + RAM_BREACH_LINE
        )
    if _ram_breach_observed(record):
        return (
            content
            + "\n\n"
            + RAM_BREACH_LINE
            + "\n"
            + RAM_PEAK_LINE.format(
                peak_bytes=int(record.observed_peak_bytes)
            )
        )
    return content


class BashInputSchema(BaseModel):
    """LLM-visible arguments for the bash tool.

    ``env`` (agent-secrets P2, design §1a): OPTIONAL overlay merged
    atop ``os.environ`` for the child shell ONLY. Values may carry
    ``__ENS_SECRET__<uuid4>__`` markers (mint via
    ``secret_store``) — markers resolve to plaintext at spawn time
    and NEVER round-trip back into the transcript (S1 scrubs
    results). A marker that fails to resolve (missing id, malformed,
    DB down) FAILS CLOSED: the command is NOT executed and a typed
    ``{ok:false, code, surface:"bash.env", message}`` envelope is
    returned. KMS-Lite markers (``__KMS_REF__…__`` /
    ``__KMS_ENV__…__``) resolve too (chained, unchanged). Markers in
    the ``command`` string are NOT resolved (plan §6.1 —
    quoting-fragile).
    """

    command: str | List[str]
    timeout: int | float | None = 1800
    workdir: str | None = None
    input: str | None = None
    env: Dict[str, str] | None = None


@register_tool_category("bash")
# ``instance_id`` is runtime-injected ownership metadata and must not be
# controllable through LLM-visible tool arguments.
@tool(args_schema=BashInputSchema)
async def bash(
    command: str | List[str],
    timeout: int | float | None = 1800,
    workdir: str | None = None,
    input: str | None = None,
    env: Dict[str, str] | None = None,
    instance_id: str | None = None,
    *,
    # ── RAM auto-termination — the INTERNAL per-call ceiling seam ──
    # SECURITY RULING (phase3-plan.md T1): this is an internal/test
    # seam ONLY.  It is deliberately NOT in ``BashInputSchema`` — an
    # agent-visible knob would let the agent raise its own ceiling.  The
    # LLM-facing schema is asserted unchanged by the test pack.
    ram_ceiling_bytes: int | None = None,
) -> str:
    """Execute a bash command and return the output. Timeout is in seconds (0-1800, default 1800). Use tool_help("bash") for details. A RAM-guarded execution whose tree exceeds the memory ceiling is terminated tree-wide and returns a typed JSON envelope (code "ram_limit_exceeded") instead of the output."""
    # Validate timeout parameter
    if timeout is not None:
        if timeout < 0:
            return f"ERROR: Timeout must be ≥ 0 seconds. Got: {timeout}s"
        if timeout > 1800:
            return f"ERROR: Timeout must be ≤ 1800 seconds. Got: {timeout}s"
    # Initialize temp-file variables before the try block so the function-level
    # finally can safely close/unlink them even if an exception occurs mid-setup
    # (e.g., the second mkstemp fails after the first succeeds).
    stdout_path = None
    stderr_path = None
    stdin_path = None
    stdout_file = None
    stderr_file = None
    stdin_file = None
    proc: asyncio.subprocess.Process | None = None
    # Captured PGID (D4: eager capture at spawn, BEFORE any await that could
    # be cancelled). Used by both the timeout-cleanup path and the
    # CancelledError handler to avoid re-deriving via ``os.getpgid(proc.pid)``
    # which can race against PID recycling if the process exited.
    pgid: int | None = None
    # D4 — bash subprocess starttime jiffies (Linux
    # ``/proc/<pid>/stat`` field 22). Captured eagerly after spawn
    # so we NEVER re-derive after the wait (M3: the read races
    # against the kernel reaping the proc). None on macOS/Windows
    # — the registry accepts None and the lookup falls through to
    # the pgid fallback.
    bash_start_time_jiffies: int | None = None
    # D4 — best-effort VmHWM captured RIGHT AFTER spawn (it grows
    # monotonically). After ``proc.wait()`` returns, /proc/<pid>
    # may already be reaped; the spawn-time snapshot is the
    # closest reliable floor.
    bash_peak_rss_kb_at_spawn: int | None = None
    # bash-tool orphan reaper Phase 1: tracking id minted pre-spawn so the
    # env stamp (task 1.4) and the durable row (F4) can both reference it.
    # Gating (instance_id present AND master switch ON) is applied at the
    # stamp/write sites; minting itself is inert.
    tracking_id: str | None = uuid4().hex if instance_id is not None else None
    # rogue-containment pkill overlay guard (adjudication 1): refuse
    # kill-like commands targeting live ensemble processes/ports/install
    # roots unless a fenced test context marker is present.
    #
    # fix-round 2 / B6: the fence is AMBIENT-ONLY. An agent-settable
    # tool-call env overlay must never carry it (that would make the guard
    # pattern-only, not containment). An overlay attempt is IGNORED and the
    # key is stripped from the child environment so it cannot be laundered
    # into a nested process.
    spawn_env_overlay = env
    if isinstance(env, dict) and "ENSEMBLE_FENCED_TEST_CONTEXT" in env:
        logger.warning(
            "bash: ENSEMBLE_FENCED_TEST_CONTEXT in the tool-call env overlay "
            "is IGNORED (the fence is ambient-only, daemon-set) and stripped "
            "from the child environment."
        )
        spawn_env_overlay = {
            k: v for k, v in env.items() if k != "ENSEMBLE_FENCED_TEST_CONTEXT"
        }
    _pkill_violation = _pkill_overlay_violation(command, spawn_env_overlay)
    if _pkill_violation is None and input is not None:
        _pkill_violation = _pkill_overlay_violation(input, spawn_env_overlay)
    if _pkill_violation is not None:
        return (
            "ERROR: refused by the rogue-containment pkill overlay guard — "
            f"command matches live-ensemble pattern {_pkill_violation!r}. "
            "This guard prevents agent-issued bash from killing live "
            "ensemble processes/ports/install roots. To run such a command "
            "inside a fenced (scratch) test mirror, set "
            "ENSEMBLE_FENCED_TEST_CONTEXT=1 in the tool env "
            '(env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}) or in the daemon '
            "environment — only inside a fenced mirror. Scratch-sentinel/"
            "scratch-named targets are allowed without the marker."
        )
    try:
        # start_new_session=True (Unix) creates a new process group so that
        # backgrounded children (e.g., `nohup ... &`) can be killed together
        # with the parent if we time out. Not supported on Windows.
        #
        # Output capture uses temp FILES (not pipes) to avoid the
        # backgrounded-child-keeps-pipe-open hang. When a shell spawns
        # `sleep 10 &`, the backgrounded grandchild inherits the shell's
        # stdout/stderr FDs. With pipes, that grandchild keeps the pipe
        # write end open, and `proc.communicate()` waits forever for EOF.
        # With regular files, the grandchild still inherits the FDs, but
        # nothing waits for the file to be "closed" — we just read the
        # file's contents after the shell exits. The grandchild holding
        # the file FD open is harmless.
        subproc_kwargs: dict = {}
        if sys.platform != "win32":
            subproc_kwargs["start_new_session"] = True

        # Open temp files for stdout and stderr capture.
        # Use tempfile.mkstemp so we get the fd and path directly. We close the
        # raw fd and reopen via Python file objects, then pass those to the
        # subprocess and unlink ourselves after reading.
        # Files are opened in read+write binary mode (O_RDWR) so the child
        # shell can write to them while the parent still holds a read-capable
        # handle.
        # w+b: binary mode since asyncio.subprocess writes raw bytes.
        stdout_fd, stdout_path = tempfile.mkstemp(prefix="bash-stdout-", suffix=".tmp")
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="bash-stderr-", suffix=".tmp")
        # Close the parent's raw fds; we'll reopen via Python file objects.
        os.close(stdout_fd)
        os.close(stderr_fd)

        stdout_file = open(stdout_path, "w+b")
        stderr_file = open(stderr_path, "w+b")

        # If input is provided, write it to a temp file and pass that to the
        # child's stdin (so the shell can read it without blocking on a pipe).
        if input:
            stdin_fd, stdin_path = tempfile.mkstemp(prefix="bash-stdin-", suffix=".tmp")
            # Use a Python file object so partial writes are handled internally.
            with os.fdopen(stdin_fd, 'wb') as f:
                f.write(input.encode())
            stdin_file = open(stdin_path, "rb")

        try:
            stdin_arg = stdin_file if stdin_file is not None else asyncio.subprocess.DEVNULL
            # ── agent-secrets P2 (design §1a) — spawn-time env chain ──
            # assembled = os.environ.copy() ⊕ env overlay →
            # resolve_secret_markers (P2, surface "bash.env") →
            # resolve_env (KMS-Lite, chained — unchanged) → spawn.
            # LITERAL §1a order: secrets-then-KMS (diverges from §1c's
            # MCP order; marker shapes are disjoint so either order is
            # correct — see the commission report for the reasoning).
            # The SecretService is resolved lazily per call — ``None``
            # fails closed ONLY when a marker-shaped value is present
            # (the resolver's lazy service check); marker-free env
            # passes through byte-identical (zero measurable latency).
            # rogue-containment (e′): ALWAYS materialise the inherit base
            # through apply_spawn_boundary so the bind/DB/attestation
            # family is stripped even when no overlay is passed. The
            # None-inherit path that carried PORT/HOST/POSTGRES_*/... is
            # collapsed. Secret-marker resolution + KMS resolution run
            # AFTER the strip (strip FIRST), then the reaper stamp.
            if not _SPAWN_BOUNDARY_AVAILABLE:  # pragma: no cover - defensive
                logger.error(
                    "bash: spawn-boundary scrub UNAVAILABLE (%s) — child "
                    "environment is NOT scrubbed for this spawn. FAIL-OPEN "
                    "by policy (availability), loudly logged.",
                    _SPAWN_BOUNDARY_IMPORT_ERROR,
                )
            assembled_env: Dict[str, str] = _apply_spawn_boundary(
                os.environ, spawn_env_overlay
            )
            assembled_env = _resolve_secret_markers(
                assembled_env,
                secret_service=_get_process_secret_service(),
                actor=instance_id or "<bash>",
                surface="bash.env",
            )
            assembled_env = _kms_resolve_env(assembled_env)
            # bash-tool orphan reaper Phase 1 (task 1.4): env stamp.
            # Gated: instance_id present AND master switch ON (F6 default ON).
            if instance_id is not None and tracking_id is not None:
                try:
                    _stamp_on = _bash_reaper_switch()
                except Exception:
                    _stamp_on = True
                if _stamp_on:
                    assembled_env[_BASH_TRACKING_ENV_VAR] = tracking_id
            # Leader ruling B: strip FOREIGN ownership stamps POST-overlay so
            # an env overlay can never forge the other lane's stamp.
            # INTEGRATION (latest-absorption merge, 2026-11-12): upstream
            # guarded this strip with materialisation branches for a
            # None-able ``assembled_env``; the rogue-containment (e′)
            # shape ABOVE always materialises (``_apply_spawn_boundary``
            # returns a dict unconditionally), so those branches are
            # dead and dropped here — the STRIP itself is unchanged
            # upstream semantics, still gated on instance-attributed
            # spawns and still POST-overlay (the overlay is applied
            # inside ``_apply_spawn_boundary``).
            if instance_id is not None:
                for v in _RAM_FOREIGN_OWNERSHIP_ENV_VARS:
                    assembled_env.pop(v, None)
            if isinstance(command, list):
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=stdin_arg,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=workdir,
                    env=assembled_env,
                    **subproc_kwargs,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdin=stdin_arg,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=workdir,
                    env=assembled_env,
                    **subproc_kwargs,
                )
        finally:
            # Parent must close its copies — the child has its own handles via
            # the inherited FDs. Closing the parent's copies ensures that when
            # the child also exits, the file is fully released.
            stdout_file.close()
            stderr_file.close()
            if stdin_file is not None:
                stdin_file.close()

        # REGISTER after spawn (always) — D4: eager PGID capture
        # + D4: pid_registry stamp with starttime jiffies + kind hint.
        # The pid_registry drives resource_pid_map persistence (the
        # BashProcessRegistry stays for the killpg sweep on shutdown —
        # it tracks ``(instance_id, pid, pgid)`` for the per-instance
        # tree-kill lane; pid_registry carries the full per-row
        # metadata: start_time, kind, command, parent_pid, peak_rss).
        if instance_id is not None and proc is not None:
            try:
                pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                pgid = proc.pid  # start_new_session=True → PGID == PID
            # Eagerly read /proc/<pid>/stat field 22 for the
            # PID-recycle defense (M3: capture NOW, never re-derive
            # after exit). Best-effort: the proc may exit between
            # spawn and this read on a near-zero-lifetime child;
            # we register start_time=0 in that edge case and the
            # registry lookup falls through to the pgid fallback.
            bash_start_time_jiffies = _read_proc_start_time(proc.pid)
            bash_peak_rss_kb_at_spawn = _read_proc_vm_hwm_kb(proc.pid)
            # ── RAM auto-termination — S1: register the spawn ──
            # The ALREADY-CAPTURED pgid (never a second ``os.getpgid``
            # — the TOCTOU the capture block respects) and the already
            # read start time.  The tracking id is the bash lane's own
            # (it is ALSO the ownership stamp the ladder's P6 check
            # matches against — one id, one spawn).
            _ram_register_spawn(
                tracking_id=tracking_id,
                instance_id=instance_id,
                pid=proc.pid,
                pgid=pgid,
                start_time_jiffies=bash_start_time_jiffies,
                ceiling_bytes=ram_ceiling_bytes,
            )
            await get_bash_process_registry().register(instance_id, proc.pid, pgid)
            # D14 alignment: sanitize the command string BEFORE
            # stamping it. We carry executable + argv-shape ONLY —
            # never the ``env`` overlay (which carries
            # __ENS_SECRET__/__KMS_REF__ markers that must never
            # land in resource_pid_map.command). For list commands
            # the literal argv is safe (no shell-expansion =
            # no secret interpolation). For shell strings we
            # truncate to argv[0] only — a full string could carry
            # ``KEY=value command`` prefixes we never want to
            # persist. The detail column carries the full command
            # shape + env-overlay keys (no values) for forensics.
            sanitized_command, detail_for_registry = _sanitize_command_for_registry(
                command, spawn_env_overlay
            )
            try:
                _pid_registry_register(
                    pid=proc.pid,
                    start_time=bash_start_time_jiffies,
                    # W-1: resolve via the instance-keyed ladder
                    # (ContextVar hint → registry map → 'bash').
                    # In the production topology the ContextVar set
                    # by the chart tool never crosses the worker-
                    # thread boundary, so the registry map (keyed
                    # by THIS instance_id == the charter child) is
                    # what makes chart renders stamp
                    # kind='chart_render'.
                    kind=_resolve_pid_kind(instance_id),
                    instance_id=instance_id,
                    pgid=pgid,
                    command=sanitized_command,
                    detail=detail_for_registry,
                    parent_pid=os.getppid(),
                )
            except Exception as exc:
                # Stamp failures MUST NOT break the bash lane —
                # log and continue. The registry is best-effort
                # observability; the bash subprocess still ran.
                logger.warning(
                    "bash: pid_registry.register failed for pid=%d "
                    "(start_time=%r): %s: %s",
                    proc.pid, bash_start_time_jiffies,
                    type(exc).__name__, exc,
                )
            # ── bash-tool orphan reaper Phase 1 (F4): durable spawn stamp ──
            # OUTSIDE the registry lock (register() above already returned),
            # AFTER the RAM append, reusing sanitized_command (no hoist).
            # Bounded wait_for(shield(to_thread(...))) so a wedged engine
            # can never hang the tool; shield alone does not cover a stuck
            # socket — the bounded wait_for is the guarantee. On timeout the
            # detached inner task still lands a best-effort late tombstone.
            # Fail-open: DB trouble ⇒ WARNING, tool result unchanged (S4).
            if _bash_reaper_switch():
                _durable_writer = get_bash_process_registry().get_durable_writer()
                if _durable_writer is not None:
                    try:
                        # minor-5: route the durable insert through the
                        # dedicated BOUNDED executor (not the loop's
                        # default executor) — a PG-outage burst queues
                        # instead of pinning default-executor threads.
                        loop = asyncio.get_running_loop()
                        await asyncio.wait_for(
                            asyncio.shield(
                                loop.run_in_executor(
                                    _DURABLE_INSERT_EXECUTOR,
                                    partial(
                                        _durable_writer.insert_open_row,
                                        pid=proc.pid,
                                        pgid=pgid,
                                        start_time=bash_start_time_jiffies,
                                        tracking_id=tracking_id,
                                        instance_id=instance_id,
                                        command_sanitized=sanitized_command,
                                        boot_id=_CURRENT_BOOT_ID,
                                        daemon_pid=os.getpid(),
                                        created_at=_now_utc_iso(),
                                    ),
                                )
                            ),
                            timeout=_DURABLE_STAMP_WAIT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "bash: durable stamp late (bounded wait exceeded "
                            "%.1fs); late tombstone lands best-effort "
                            "(pid=%d pgid=%s tracking=%.8s)",
                            _DURABLE_STAMP_WAIT_SECONDS,
                            proc.pid, pgid, tracking_id or "",
                        )
                    except Exception as exc:
                        logger.warning(
                            "bash: durable spawn stamp failed (fail-open): "
                            "%s: %s (pid=%d pgid=%s)",
                            type(exc).__name__, exc, proc.pid, pgid,
                        )
        elif instance_id is None:
            logger.warning("bash: instance_id is None; skipping process registration")
        # timeout=0 means "no timeout" — pass None to wait_for
        actual_timeout = None if timeout == 0 else timeout
        try:
            await asyncio.wait_for(proc.wait(), timeout=actual_timeout)
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True

        # ── The timeout arm's SIGNAL — before the verdict wait ──
        # The episode-scoped claim token (arch §17.5): the FIRST
        # claimant owns the verdict and a loser SKIPS its signal, so
        # the timeout arm and the guard's ladder never BOTH signal
        # (test-plan O9).  The guard's claim is read WITHOUT waiting.
        timeout_kill_deferred = False
        if timed_out:
            if _ram_claim_held(
                _read_ram_verdict(tracking_id) if tracking_id else None
            ):
                timeout_kill_deferred = True
            else:
                # M3: pass captured pgid to avoid a second ``os.getpgid(proc.pid)``
                # call that could race against PID recycling if the process just
                # exited between the SIGTERM and the SIGKILL window.
                await _kill_process(proc, pgid=pgid)

        # ── S2 — the guard's verdict (the ONLY RAM discriminator) ──
        # The await has returned: the guard's execution-lane thread may
        # ALREADY have killed the tree (arch §4.3.1).  The verdict
        # RECORD decides which arm runs — the exit code NEVER does
        # (``-9`` is ambiguous by construction, arch §5).  The wait is
        # bounded (1308 ms) and async (the event loop is never blocked).
        ram_record = await _await_ram_verdict(tracking_id)
        ram_kill = _ram_is_kill(ram_record)
        ram_breach = _ram_breach_observed(ram_record)

        if timed_out and timeout_kill_deferred and not ram_kill:
            # The guard REFUSED the kill (it never signalled) — the
            # timeout arm's own kill contract still stands.
            await _kill_process(proc, pgid=pgid)

        if ram_kill:
            # ── The RAM arm (S4/S6) — it PREEMPTS the success arm, the
            # ``EXIT CODE:`` line, the 150 k truncation arm and the
            # timeout arm (arch §4.3 precedence). ──
            # T5 — the verdict is PUBLISHED (observed terminal) BEFORE
            # any mark_exit / registry release: the release sequence
            # below runs after it, and the success arm's mark_exit does
            # NOT run (the one-shot mark_exit would erase the verdict).
            await _ram_release_sequence(
                tracking_id=tracking_id,
                instance_id=instance_id,
                pid=proc.pid,
                start_time_jiffies=bash_start_time_jiffies,
                exit_code=proc.returncode,
                peak_bytes=int(ram_record.observed_peak_bytes),
            )
            return _format_ram_envelope(
                ram_record,
                ram_breach_observed=True,
                coincident_timeout=bool(timed_out),
                timeout_seconds=(
                    float(timeout) if timed_out and timeout else None
                ),
            )

        if timed_out:
            if instance_id is not None:
                await get_bash_process_registry().unregister(instance_id, proc.pid)
                # D5 — route the timeout arm through mark_exit so
                # ``exit_kind`` is uniform with the success path.
                # Best-effort: a starttime race or a missing live
                # entry is logged at DEBUG, never re-raised.
                _pid_registry_mark_exit(
                    pid=proc.pid,
                    start_time=bash_start_time_jiffies,
                    exit_code=proc.returncode,
                    exit_kind=EXIT_KIND_TIMEOUT,
                    peak_rss_kb=bash_peak_rss_kb_at_spawn,
                )

        # D5 — SUCCESS-PATH EXIT-MARK (the documented leak).
        # Before this fix, ``if not timed_out:`` fell through to
        # ``return content`` with NO unregister on EITHER registry —
        # the live dict grew by one entry per bash call. The mark
        # is guarded by the eager-captured ``bash_start_time_jiffies``
        # so a recycled pid cannot be mis-marked (mark_exit
        # verifies pid+start_time before touching the entry —
        # see PidRegistry.mark_exit).
        #
        # Peak VmHWM: try one best-effort /proc read AFTER wait()
        # returns; if the kernel has already reaped the proc the
        # read returns None and we fall back to the spawn-time
        # floor captured above (VmHWM is monotonic, so the spawn
        # floor is the tightest lower bound we can guarantee).
        #
        # N-3 (P2 review, comment-only caveat): that post-wait read
        # ~always MISSES in practice — wait() reaps the child before
        # this line runs, so /proc/<pid>/status is gone and the
        # column lands on the SPAWN-TIME floor. Treat
        # ``peak_rss_kb`` as an honest UNDER-REPORT (a lower bound
        # near the exec floor), never a steady-state peak; a true
        # VmHWM would need pre-reap polling the bash lane does not
        # do. No behavior change.
        if not timed_out and instance_id is not None and proc is not None:
            post_wait_peak_kb = _read_proc_vm_hwm_kb(proc.pid)
            peak_kb_for_exit = (
                post_wait_peak_kb
                if post_wait_peak_kb is not None
                else bash_peak_rss_kb_at_spawn
            )
            _pid_registry_mark_exit(
                pid=proc.pid,
                start_time=bash_start_time_jiffies,
                exit_code=proc.returncode,
                exit_kind=EXIT_KIND_SUCCESS,
                peak_rss_kb=peak_kb_for_exit,
            )
            # N-1 (P2 review): the success arm must ALSO release the
            # OLD BashProcessRegistry entry — before this line the
            # pid_registry was marked but the (instance_id, pid,
            # pgid) killpg-tracking entry leaked one row per
            # successful call until cleanup_instance. Mirror of the
            # timeout arm's unregister (which already runs after
            # _kill_process). Best-effort: a failure here must not
            # break the bash lane's return path.
            try:
                await get_bash_process_registry().unregister(
                    instance_id, proc.pid
                )
            except Exception as exc:
                logger.warning(
                    "bash: BashProcessRegistry.unregister failed on the "
                    "success arm (instance=%.8s pid=%d): %s: %s",
                    instance_id, proc.pid, type(exc).__name__, exc,
                )

        # Read captured output from the temp files (best-effort: also try on
        # timeout to surface whatever was written before the kill).
        stdout_bytes = _read_file_bytes(stdout_path)
        stderr_bytes = _read_file_bytes(stderr_path)
        stdout_str = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr_str = stderr_bytes.decode(errors="replace") if stderr_bytes else ""

        output_parts = []
        if stdout_str:
            output_parts.append(f"STDOUT:\n{stdout_str}")
        if stderr_str:
            output_parts.append(f"STDERR:\n{stderr_str}")
        if not timed_out:
            output_parts.append(f"EXIT CODE: {proc.returncode}")
        content = "\n\n".join(output_parts)

        if timed_out:
            if ram_breach:
                # The COINCIDENCE — the timeout won the race against the
                # guard (arch §17.5 / test-plan O7): the code follows the
                # claim token and the observed breach is reported beside
                # the peak.  Without a breach the legacy timeout string
                # is returned BYTE-IDENTICALLY (it has zero test pins,
                # so only the breach case may change it).
                return _format_ram_envelope(
                    ram_record,
                    code=RAM_CODE_TIMEOUT,
                    ram_breach_observed=True,
                    coincident_timeout=True,
                    timeout_seconds=(float(timeout) if timeout else None),
                )
            return content + f"ERROR: Command timed out after {timeout} seconds"

        # Apply character limit only (no line limit)
        if len(content) > 150000:
            truncated = content[:150000] + "\n\n--- OUTPUT TRUNCATED ---"
            hint = """
**⚠️ Output truncated at 150,000 characters.**

For full output, redirect to file:
  `command > /tmp/output.txt` then use `read_file`
"""
            content = truncated + hint
        # ── The additive RAM surfaces (arch §15 B25) — they ride AFTER
        # the truncation so they are never swallowed, and they are
        # ADDITIVE: the command's output is never swallowed. ──
        return _ram_additive_surface(content, ram_record)

    except asyncio.CancelledError:
        # Cancellation at EITHER await point (spawn or wait_for).
        if proc is not None:
            # Clear sticky cancellation so _kill_process's internal awaits
            # don't immediately re-raise CancelledError (Python 3.11+).
            task = asyncio.current_task()
            if task is not None and hasattr(task, "uncancel"):
                task.uncancel()
            # M2: split kill + unregister into INDEPENDENT guarded blocks so
            # a second cancellation arriving mid-kill cannot skip the
            # ``unregister`` call and leave a stale registry entry behind.
            # ``_kill_process`` failure must not block the registry cleanup.
            try:
                # shield protects against concurrent second cancel; M3
                # passes the eager-captured pgid to avoid the
                # ``os.getpgid(proc.pid)`` TOCTOU race.
                await asyncio.shield(_kill_process(proc, pgid=pgid))
            except BaseException:
                pass  # best-effort during cancellation
            if instance_id is not None:
                try:
                    await asyncio.shield(
                        get_bash_process_registry().unregister(instance_id, proc.pid)
                    )
                except BaseException:
                    pass  # best-effort during cancellation
                # D5 — route the cancellation arm through mark_exit
                # with EXIT_KIND_CANCELLED. Best-effort, idempotent
                # (mark_exit returns None when no live entry matches
                # the captured starttime).
                try:
                    _pid_registry_mark_exit(
                        pid=proc.pid,
                        start_time=bash_start_time_jiffies,
                        exit_code=proc.returncode,
                        exit_kind=EXIT_KIND_CANCELLED,
                        peak_rss_kb=bash_peak_rss_kb_at_spawn,
                    )
                except BaseException:
                    pass  # best-effort during cancellation
        raise  # ALWAYS re-propagate the cancellation
    except SecretMarkerResolutionError as secret_env_exc:
        # agent-secrets P2 (design §1a) — typed envelope at the
        # existing error site. Short-circuits BEFORE the generic
        # ``ERROR: …`` string so the agent-visible payload carries
        # the typed ``{ok:false, code, surface:"bash.env", message}``
        # envelope; the child was NOT spawned (the resolve raised
        # before ``create_subprocess_*`` — never partial env).
        return _format_secret_envelope(secret_env_exc)
    except Exception as e:
        # phase3-plan.md T8 — the generic-exception arm releases NEITHER
        # registry today, and ``MemoryError`` lands exactly here during
        # a RAM incident.  The guard release is added (the other two
        # registries' pre-existing leak is DISCLOSED, not fixed here —
        # it is routed to the Phase 7 follow-up list).  The release is
        # CONDITIONAL: it never ends a live watch and a claimed
        # record's release stays deferred to the lane (arch §15 B21).
        # (The finally-shaped release below is the general backstop for
        # all six exit paths; this is the arm's own bookkeeping.)
        _ram_lane_release(tracking_id)
        return f"ERROR: {str(e)}"
    finally:
        # ── The lane's finally-shaped guard release (T8) ──
        # It covers ALL SIX bash exit paths (success, timeout, RAM,
        # cancel, generic-exception, spawn-failure).  The release is
        # CONDITIONAL on the watch being finished: a lane exit is a
        # NOTIFICATION, never the end of a live watch, and a CLAIMED
        # record's release is deferred to the lane (arch §4.3.1 item 4
        # / §15 B21).  Best-effort: it can never break the return path.
        _ram_lane_release(tracking_id)
        # Clean up temp files.
        for f in (stdout_file, stderr_file, stdin_file):
            if f is None:
                continue
            try:
                f.close()
            except OSError:
                pass
        for path in (stdout_path, stderr_path, stdin_path):
            if path is None:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass

bash._full_doc_ = """Execute a bash command and return the output.

**Agent Guidelines:**
- Avoid blocking/long-running commands (e.g., `tail -f`, `watch`, interactive editors) — except via skills that use CLI.
- If output may be large or truncated, write to a file and use `read_file` instead.
- Example for large output: `command > /tmp/output.txt && echo "Saved to /tmp/output.txt"`

Args:
    command: The bash command to execute. Can be:
        - A string (interpreted as shell command with shell=True)
        - A list of strings like ["ls", "-la", "path"] (no shell interpretation)
    timeout: Maximum time to wait (in seconds). Must be 0-1800. Default: 1800 (30 minutes)
    workdir: Working directory for command execution (default: current directory)
    input: Optional string to pass to stdin
    env: Optional dict merged atop os.environ for the child shell ONLY.
        Values may carry __ENS_SECRET__<uuid4>__ markers (mint via
        secret_store) — markers resolve to plaintext at spawn time and
        never echo back into the transcript. KMS-Lite markers
        (__KMS_REF__…__ / __KMS_ENV__…__) resolve too. A failing marker
        (missing id, malformed, DB down) FAILS CLOSED: the command is
        NOT executed and a typed {ok:false, code, surface:"bash.env"}
        envelope is returned. Markers inside the `command` string are
        NOT resolved.

Returns:
    Command output including stdout, stderr, and exit code.
    A RAM-guarded execution whose process tree exceeds the memory ceiling is
    terminated tree-wide and returns a typed JSON envelope INSTEAD of the
    output: {"ok": false, "code": "ram_limit_exceeded", "surface": "bash.exec",
    "reason": ..., "limit_bytes": ..., "rearm_threshold_bytes": ...,
    "observed_peak_bytes": ..., ...}. Parse it as JSON — it is the whole return.
    Output is truncated at 150,000 characters — redirect to file for full results.
"""


# ---------------------------------------------------------------------------
# rogue-containment pkill overlay guard (adjudication 1, iter-1 hardened)
# ---------------------------------------------------------------------------
# Pattern gate only — NO PID-identity resolution. Refuses bash commands that
# look like process/port kills targeting LIVE ensemble processes, ports, or
# install roots, unless an explicit fenced-test context marker is present.
#
# Fence mechanism (documented for the census walk):
#   - ambient daemon env ENSEMBLE_FENCED_TEST_CONTEXT=1 ONLY.
#   The tool-call env overlay is NOT a fence channel (fix-round 2 / B6): an
#   overlay attempt is ignored (and the key is stripped from the child env),
#   so the guard cannot be self-granted by the agent it constrains.
# Scratch-sentinel / scratch-named targets (no live pattern) pass without
# the marker.
#
# Residuals / evasion surface (documented for the census walk, N10):
#   - two-step PID laundering (``lsof -ti:9797`` then ``kill <pid>``): the
#     gate is pattern-based, not PID-identity based;
#   - regex variants that do not contain a pattern token verbatim
#     (``pkill -f 'ensemble.*prod'``);
#   - substring collisions (``ensemble-maintenance`` does NOT match
#     ``ensemble-main`` as a whole token in the command string? it does —
#     conservative over-refusal is accepted);
#   - ``kill -9 -1`` (signal every process the uid may signal);
#   - same-uid / same-install limits (see port_ownership residuals).
# The ownership claim (component A) is the control that does not depend on
# command text; this overlay is defense-in-depth only.
#
# Word-boundary / anchored design (iter-1 D2c):
#   - kill-like verbs match as command words only, so "skill " does NOT
#     trigger (\b boundary prevents the substring match inside "skill").
#   - port tokens match only as whole words (\b9797\b), so "19797" does
#     not trigger.
#   - systemctl: read-only verbs (status/show/is-active/list-units/cat/
#     is-enabled/is-failed/get-default) are ALLOWED against live units;
#     mutating verbs (stop/restart/kill/disable/mask/reload/daemon-reload/
#     enable/start/try-restart/isolate) are gated.
#
# Live patterns (iter-1 D2a adds ensemble-main, ensemble-demo):
#   "ensemble-prod", "ensemble-daemon", "ensemble-main", "ensemble-demo",
#   "\b9797\b", "\b9798\b", "agents-ensemble"

import re as _re

_PKILL_OVERLAY_LIVE_NAME_PATTERNS: tuple[str, ...] = (
    "ensemble-prod",
    "ensemble-daemon",
    "ensemble-main",
    "ensemble-demo",
    "agents-ensemble",
)

_PKILL_OVERLAY_SYSTEMCTL_MUTATING: frozenset[str] = frozenset({
    "stop", "restart", "kill", "disable", "mask", "reload",
    "daemon-reload", "enable", "start", "try-restart", "isolate",
})

_PKILL_OVERLAY_SYSTEMCTL_READONLY: frozenset[str] = frozenset({
    "status", "show", "is-active", "list-units", "list-unit-files",
    "cat", "is-enabled", "is-failed", "get-default", "help",
})


def _pkill_overlay_is_kill_like(cmd_lower: str) -> bool:
    # kill-like verbs as command words (word-boundary).
    if _re.search(r"\bpkill\b", cmd_lower):
        return True
    if _re.search(r"\bkillall\b", cmd_lower):
        return True
    if _re.search(r"\bfuser\b", cmd_lower):
        return True
    # "kill" as a command word: preceded by start/whitespace/;&|(` then
    # optional spaces; not part of another word.
    if _re.search(r"(^|[\s;&|(`])\s*kill\b", cmd_lower):
        return True
    if "xargs kill" in cmd_lower or _re.search(r"\bxargs\s+kill\b", cmd_lower):
        return True
    if "systemctl" in cmd_lower:
        # only mutating verbs are gated; read-only verbs pass
        for verb in _PKILL_OVERLAY_SYSTEMCTL_MUTATING:
            if _re.search(r"\b" + _re.escape(verb) + r"\b", cmd_lower):
                return True
        return False
    return False


def _pkill_overlay_violation(command, env=None) -> str | None:
    """Return the matched live pattern if the command must be refused,
    else None. Pure function (no I/O) so it is unit-testable."""
    if isinstance(command, (list, tuple)):
        cmd_str = " ".join(str(c) for c in command)
    else:
        cmd_str = str(command)
    lower = cmd_str.lower()
    if not _pkill_overlay_is_kill_like(lower):
        return None
    # fix-round 2 / B6: the fence is read from the DAEMON-SET AMBIENT
    # environment ONLY. ``env`` (the agent-settable tool-call overlay) is
    # accepted for call-site compatibility but is DELIBERATELY IGNORED as a
    # fence source — otherwise any agent could grant itself a fence in the
    # same call that issues a live-pattern kill.
    del env
    fenced = os.environ.get("ENSEMBLE_FENCED_TEST_CONTEXT") == "1"
    if fenced:
        return None
    for name in _PKILL_OVERLAY_LIVE_NAME_PATTERNS:
        if name in lower:
            return name
    # port tokens as whole words
    for port_num, port_pat in (("9797", r"\b9797\b"), ("9798", r"\b9798\b")):
        if _re.search(port_pat, lower):
            return port_num
    return None
