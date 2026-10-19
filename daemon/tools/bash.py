"""Bash execution tool for running shell commands."""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

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
    EXIT_KIND_SUCCESS,
    EXIT_KIND_TIMEOUT,
    KIND_BASH,
    KIND_CHART_RENDER,
    get_pid_registry,
)

logger = logging.getLogger(__name__)


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

    async def cleanup_instance(self, instance_id: str) -> int:
        """Kill all tracked process groups for one instance."""
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
        return killed

    async def cleanup_all(self) -> int:
        """Kill every tracked bash process group during daemon shutdown.

        Known limitations:

        * Truly-detached orphans whose child called ``setsid`` sit outside
          the original process group, so ``killpg`` cannot reach them.
        * Crash-recovery leak: this in-memory registry does not survive a
          daemon restart, so processes from a hard crash cannot be enumerated.
        """
        async with self._lock:
            instance_ids = list(self._entries.keys())

        total = 0
        for iid in instance_ids:
            try:
                total += await self.cleanup_instance(iid)
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
) -> str:
    """Execute a bash command and return the output. Timeout is in seconds (0-1800, default 1800). Use tool_help("bash") for details."""
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
            assembled_env: Dict[str, str] | None = None
            if env:
                assembled_env = os.environ.copy()
                # Overlay wins over os.environ (§1a semantics).
                assembled_env.update(env)
                assembled_env = _resolve_secret_markers(
                    assembled_env,
                    secret_service=_get_process_secret_service(),
                    actor=instance_id or "<bash>",
                    surface="bash.env",
                )
                assembled_env = _kms_resolve_env(assembled_env)
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
                command, env
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
        elif instance_id is None:
            logger.warning("bash: instance_id is None; skipping process registration")
        # timeout=0 means "no timeout" — pass None to wait_for
        actual_timeout = None if timeout == 0 else timeout
        try:
            await asyncio.wait_for(proc.wait(), timeout=actual_timeout)
            timed_out = False
        except asyncio.TimeoutError:
            # M3: pass captured pgid to avoid a second ``os.getpgid(proc.pid)``
            # call that could race against PID recycling if the process just
            # exited between the SIGTERM and the SIGKILL window.
            await _kill_process(proc, pgid=pgid)
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
            timed_out = True

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
            return content + f"ERROR: Command timed out after {timeout} seconds"

        # Apply character limit only (no line limit)
        if len(content) > 150000:
            truncated = content[:150000] + "\n\n--- OUTPUT TRUNCATED ---"
            hint = """
**⚠️ Output truncated at 150,000 characters.**

For full output, redirect to file:
  `command > /tmp/output.txt` then use `read_file`
"""
            return truncated + hint
        return content

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
        return f"ERROR: {str(e)}"
    finally:
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
    Output is truncated at 150,000 characters — redirect to file for full results.
"""
