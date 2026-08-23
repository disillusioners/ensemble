"""System Upgrade tools — read-only release/upgrade observability (P2.2 Dispatch A).

Category ``system_upgrade`` per the self-restart/upgrade Phase-2 design
(``.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md``).
THIS dispatch implements ONLY the read pair:

* ``release_info``  — §2.3: releases / current / journal / changelog views.
* ``upgrade_status`` — §2.4: run-scoped pipeline poller (journal + lock tail).

The actor tools (``system_restart`` / ``system_upgrade``), the journal
write-side helpers (temp+rename writes, ``pending_restart`` marker,
``confirmed_by_human``), the daemonized executor and the nonce /
``USER_ORIGIN_SOURCES`` gate are **Dispatch B** — deliberately absent here.

READ-ONLY BY CONSTRUCTION (hard constraint, live-safe):
    * The module performs NO mutations of any kind — no process signals
      (``os.kill`` is never imported/called), no lock acquisition, no
      journal/state writes, no ENSEMBLE_DEPLOY_LIVE access, no subprocesses.
    * Journal discipline (ADR-034): this dispatch NEVER writes or splices
      ``releases/state.json``; reads are open()-read-parse only, with the
      same torn-write rejection semantics as ``scripts/upgrade/lib.sh``
      ``journal_read`` (empty file or unparseable JSON → "torn", untrusted).
    * The only network touch is an OPTIONAL self-probe of the daemon's OWN
      ``/livez`` + ``/readyz`` ports (D8/§7 observability) — localhost,
      resolved from the running daemon's own ``config.daemon.port``, short
      timeout. It can never address another environment's port.
    * Self-match rule (§3.2, reviewer ruling 2026-08-22 — reads included):
      ``target_env`` must equal the daemon's own env (resolved from the
      staged ``ENSEMBLE_SELF_ENV`` marker, D-FA2.3 — PORT-derivation
      fallback REJECTED). Cross-env reads are refused structurally with
      ``env-self-match``; a dev/demo/sandbox daemon can NEVER address live,
      whatever parameters the LLM passes.
    * Marker absent → read tools STILL answer (fail-open for reads only;
      the ``env-marker-absent`` fail-closed refusals are ACTOR-tool
      behavior, Dispatch B). The unresolved self-env is represented as
      ``env=unresolved (dev-context)`` and only ``target_env`` omitted or
      ``"dev"`` is accepted in that state.

All state read here is the REAL P2.1 pipeline state — journal schema,
releases/ layout, manifests, lock protocol and ``.launcher-state`` keys
follow ``scripts/upgrade/lib.sh`` / ``stage.sh`` / ``status.sh`` exactly;
no fields are invented (the journal schema contract is the comment above
``journal_init`` in lib.sh).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "System Upgrade"
CATEGORY_DOC = """\
Read-only visibility into the staged release/upgrade pipeline (P2.1).

- `release_info` — current release, staged releases + manifests, journal
  state (in-flight txn, rollback cap/cooldown, quarantine), pipeline lock,
  launcher state, and the daemon's own /livez + /readyz probes.
- `upgrade_status` — poll a pipeline run: in-flight txn phase, journal
  history tail, terminal outcome (committed / rolled-back / halted).

Both tools target ONLY the running daemon's own environment
(``target_env`` must equal the staged ENSEMBLE_SELF_ENV marker);
cross-env reads are refused. They never mutate: no signals, no locks,
no journal writes. Actor tools (system_restart / system_upgrade) arrive
in a later dispatch.
"""

# ─── P2.2 REGISTRATION CHECKLIST (T2 — re-verify on every tool change) ───────
# Source: tool-api-design.md §3.3/§8, adapted to the Dispatch-A read pair.
# Later dispatches adding system_restart / system_upgrade MUST re-run every
# step below (steps marked [B] belong to the actor-tool dispatch).
#
#  1. daemon/tools/upgrade_tools.py — factory create_upgrade_tools() builds
#     every @tool inside the closure; each tool carries
#     @register_tool_category("system_upgrade") ABOVE @tool.            [DONE A]
#  2. daemon/tools/_tool_registry.py CATEGORY_MODULES entry:
#     "system_upgrade": "daemon.tools.upgrade_tools".                  [DONE A]
#  3. daemon/tools/_tool_registry.py DYNAMIC_TOOL_NAMES += the tool
#     names (factory-created, not import-time registered).             [DONE A]
#  4. daemon/tools/_tool_registry.py KNOWN_TOOL_NAMES — REGENERATE via:
#     uv run python -c "from daemon.tools._tool_registry import
#     discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"
#     and paste; drift test tests/unit/tools/test_frozen_tool_name_discovery.py
#     ::test_known_tool_names_matches_source_exactly_no_drift must pass. [DONE A]
#  5. daemon/tools/instance.py create_instance_tools() — the CRITICAL
#     list-append (decorator-only = silently invisible):
#     tools.extend(create_upgrade_tools(...)).                          [DONE A]
#  6. tools.allow expansion — instance.py resolve_tool_filter expands the
#     category name to its tool set; no code change needed per category. [DONE A]
#  7. PRIVILEGED_TOOL_CATEGORIES (R-SR16 / §3.5): _tool_registry.py
#     frozenset {"system_upgrade"} consumed by instance.py's empty-allow
#     branch + default-allow paths — the category is opt-in-only; agents
#     reach it ONLY via an explicit tools.allow entry.                  [DONE A]
#  8. agents/ari/meta.json tools.allow += "system_upgrade" + Ari prompt
#     fragment (confirmation protocol).                                 [DEFERRED — B]
#  9. Actor tools system_restart / system_upgrade bodies + write-side
#     journal helpers + daemonized executor + nonce gate.               [DEFERRED — B]
# 10. Docs: CATEGORY_DOC above; docs/ has no per-tool catalog to update
#     (tool_help reads _full_doc_).                                     [DONE A]
# ─────────────────────────────────────────────────────────────────────────────


VALID_ENVS: tuple[str, ...] = ("dev", "demo", "live", "sandbox")

# Env triple table mirroring scripts/upgrade/lib.sh resolve_env (display
# parity with status.sh "resolved env:" line). NOTE: dev has NO staged
# install dir (the repo checkout is not a releases/ install); the probe
# port is always the daemon's OWN serving port (manager config), never
# derived from a guessable table entry.
_ENV_TRIPLE: dict[str, dict[str, str | None]] = {
    "dev": {"dir": None, "db": "ensemble_dev"},
    "demo": {"dir": "~/agents-ensemble-demo", "db": "ensemble_demo"},
    "live": {"dir": "~/agents-ensemble", "db": "ensemble_prod"},
    "sandbox": {"dir": None, "db": "ensemble_sandbox"},  # dir: frozen-binary derived
}

# Rollback window constants (display parity — authoritative values live in
# scripts/upgrade/lib.sh: ROLLBACK_CAP_24H / COOLDOWN_S).
ROLLBACK_CAP_24H = 3

# Protocol artifacts under releases/ that are NOT releases (status.sh
# labels them separately; they must never read as releases).
_PROTOCOL_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("rollback.lock.d", "pipeline lock, not a release"),
    ("rollback.lock.d.stale.", "stale-broken pipeline lock, not a release"),
    (".staging.", "stage temp assembly, not a release"),
)

_UPGRADE_LOG_REL = Path("data/upgrade.log")
_UPGRADE_LOG_TAIL_CHARS = 256 * 1024  # bounded tail read: last 256KB max
_UPGRADE_LOG_TAIL_LINES = 50
_PROBE_TIMEOUT_S = 3.0


def _self_env_marker() -> str | None:
    """Resolve the staged self-env marker (D-FA2.3).

    The marker ``ENSEMBLE_SELF_ENV=dev|demo|live|sandbox`` is staged into
    ``INSTALL_DIR/.env`` by ``scripts/upgrade/stage.sh`` and exported into
    the daemon process by ``launcher.sh`` ``load_env_file`` (exports win
    over the frozen binary's own .env load). Reading the exported variable
    IS reading the staged marker. PORT-derivation fallback is REJECTED by
    ruling — absent marker stays absent (never guessed).
    """
    raw = (os.environ.get("ENSEMBLE_SELF_ENV") or "").strip()
    if not raw:
        return None
    return raw if raw in VALID_ENVS else None


def _resolve_install_dir(self_env: str | None) -> Path | None:
    """Resolve the SELF env's install dir (topology mirrors lib.sh resolve_env).

    demo/live use the fixed topology table; sandbox derives from the frozen
    binary location (``INSTALL_DIR/current/ensemble-prod`` → parent.parent)
    — a sandbox daemon is always a staged frozen install with an explicit
    throwaway dir; dev / unresolved have no staged install dir at all.
    Returns ``None`` when no staged install applies (read tools then answer
    honestly: "not in staged mode").
    """
    if self_env == "demo":
        return Path.home() / "agents-ensemble-demo"
    if self_env == "live":
        return Path.home() / "agents-ensemble"
    if self_env == "sandbox" and getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent.parent
        return candidate if (candidate / "releases").is_dir() else None
    return None


def _parse_iso_utc(ts: Any) -> datetime | None:
    """Parse a journal ISO timestamp (``YYYY-MM-DDTHH:MM:SSZ``); None on garbage.

    Mirrors lib.sh ``_iso_to_epoch`` fail-closed discipline: an unparseable
    timestamp must never be treated as fresh/aged — callers display
    "unknown" instead of guessing an elapsed.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _journal_read(install_dir: Path | None) -> tuple[dict[str, Any] | None, str]:
    """Torn-safe READ of ``<install_dir>/releases/state.json``.

    Returns ``(data, status)`` with status in ``{"ok", "absent", "torn"}``.
    Semantics mirror lib.sh ``journal_read``: absent → no journal yet;
    empty file or unparseable/unbalanced JSON → torn (treat as untrusted —
    pipeline mutations refuse; reads just report it). NEVER writes,
    NEVER splices, NEVER locks.
    """
    if install_dir is None:
        return None, "no-install-dir"
    jp = install_dir / "releases" / "state.json"
    try:
        if not jp.is_file():
            return None, "absent"
        raw = jp.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("upgrade_tools: journal read failed at %s: %s", jp, exc)
        return None, "unreadable"
    if not raw.strip():
        return None, "torn"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "torn"
    if not isinstance(data, dict):
        return None, "torn"
    return data, "ok"


@dataclass
class _ReleaseSummary:
    """One releases/ entry — release OR labelled protocol artifact."""

    name: str
    is_release: bool = True
    artifact_note: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    manifest_error: str = ""
    quarantined: bool = False
    is_previous: bool = False


def _scan_releases(
    install_dir: Path | None, journal: dict[str, Any] | None
) -> list[_ReleaseSummary]:
    """Scan ``releases/`` and summarize manifests. Read-only.

    Classification mirrors status.sh: ``rollback.lock.d`` (+ stale-break
    leftovers) and ``.staging.*`` temp assemblies are protocol artifacts,
    labelled separately; everything else is a release summarized from its
    manifest.json (identity fields only — the P2.1 manifest has NO
    changelog text field, so none is invented).
    """
    if install_dir is None:
        return []
    rel_dir = install_dir / "releases"
    if not rel_dir.is_dir():
        return []

    quarantined: list[str] = []
    previous: str | None = None
    if isinstance(journal, dict):
        q = journal.get("quarantined")
        if isinstance(q, list):
            quarantined = [str(v) for v in q]
        prev = journal.get("previous")
        if isinstance(prev, str):
            previous = prev

    out: list[_ReleaseSummary] = []
    try:
        entries = sorted(p.name for p in rel_dir.iterdir() if p.is_dir())
    except OSError as exc:
        logger.warning("upgrade_tools: releases scan failed at %s: %s", rel_dir, exc)
        return []

    for name in entries:
        summary = _ReleaseSummary(name=name)
        matched_artifact = False
        for prefix_or_exact, note in _PROTOCOL_ARTIFACTS:
            if name == prefix_or_exact or (
                prefix_or_exact.endswith(".") and name.startswith(prefix_or_exact)
            ):
                summary.is_release = False
                summary.artifact_note = note
                matched_artifact = True
                break
        if matched_artifact:
            out.append(summary)
            continue

        summary.quarantined = name in quarantined
        summary.is_previous = previous is not None and name == previous
        mp = rel_dir / name / "manifest.json"
        try:
            if mp.is_file():
                loaded = json.loads(mp.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    summary.manifest = loaded
                else:
                    summary.manifest_error = "manifest is not a JSON object"
            else:
                summary.manifest_error = "manifest.json missing"
        except (OSError, json.JSONDecodeError) as exc:
            summary.manifest_error = f"manifest unreadable/torn: {exc}"
        out.append(summary)
    return out


def _format_release_line(summary: _ReleaseSummary) -> str:
    """One releases-inventory line — §2.3 example shape + status.sh fields."""
    if not summary.is_release:
        return f"  {summary.name}  [protocol artifact — {summary.artifact_note}]"
    m = summary.manifest
    parts: list[str] = [summary.name]
    staged = m.get("staged_at") if m else None
    if staged:
        parts.append(f"staged={staged}")
    rb = m.get("rollback_safe") if m else None
    parts.append(f"rollback_safe={'?' if rb is None else str(rb).lower()}")
    gen = m.get("known_schema_gen") if m else None
    if gen is not None:
        parts.append(f"known_schema_gen={gen}")
    bv = m.get("binary_version") if m else None
    if bv:
        parts.append(f"binary_version={bv}")
    if summary.is_previous:
        parts.append("previous (rollback target, pinned — not evictable)")
    if summary.quarantined:
        parts.append("[QUARANTINED]")
    if summary.manifest_error:
        parts.append(f"({summary.manifest_error})")
    return "  " + " ".join(parts)


def _in_flight_summary(journal: dict[str, Any] | None) -> str:
    """The journal line fragment for in_flight — 'none' or the txn fields."""
    if not isinstance(journal, dict):
        return "unknown (journal unreadable)"
    inf = journal.get("in_flight")
    if not isinstance(inf, dict):
        return "none"
    started = inf.get("started_at", "?")
    flipped = str(inf.get("flipped", "?")).lower()
    return (
        f"{inf.get('kind', '?')} target={inf.get('target', '?')} "
        f"started_at={started} flipped={flipped} owner_pid={inf.get('owner_pid', '?')}"
    )


def _rollback_window_summary(journal: dict[str, Any] | None) -> str:
    if not isinstance(journal, dict):
        return "rollbacks_24h=? cooldown=?"
    rwc = journal.get("rollback_window_count")
    count = rwc.get("24h") if isinstance(rwc, dict) else None
    cooldown = journal.get("cooldown_until")
    return f"rollbacks_24h={count if count is not None else '?'}/{ROLLBACK_CAP_24H} cooldown={cooldown or 'none'}"


def _history_tail(journal: dict[str, Any] | None, tail: int) -> list[str]:
    """Formatted newest-last history tail: '  <ts> <event> — <detail>'."""
    if not isinstance(journal, dict):
        return []
    history = journal.get("history")
    if not isinstance(history, list):
        return []
    lines = []
    for entry in history[-tail:] if tail > 0 else []:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"  {entry.get('ts', '?')} {entry.get('event', '?')} — {entry.get('detail', '')}"
        )
    return lines


def _current_symlink(install_dir: Path | None) -> tuple[str | None, str]:
    """Read the current symlink. Returns (target, note); note '' when clean."""
    if install_dir is None:
        return None, ""
    cur = install_dir / "current"
    try:
        if not cur.is_symlink():
            return None, ""
        target = os.readlink(cur)
        name = target.rsplit("/", 1)[-1]
        if not (install_dir / "releases" / name).is_dir():
            return target, (
                "current symlink DANGLING (target release missing — layout "
                "divergence, mutations frozen per D-FA5.3)"
            )
        return target, ""
    except OSError as exc:
        logger.warning("upgrade_tools: current symlink read failed: %s", exc)
        return None, "current symlink unreadable"


def _lock_state(install_dir: Path | None) -> str:
    """Pipeline lock display — status.sh parity. READ-ONLY (no mkdir/mv/rm)."""
    if install_dir is None:
        return "pipeline lock: unknown (no install dir)"
    lock = install_dir / "releases" / "rollback.lock.d"
    if not lock.is_dir():
        return "pipeline lock: free"

    def _cat(name: str) -> str:
        try:
            return (lock / name).read_text(encoding="utf-8").strip()
        except OSError:
            return "?"

    heartbeat = _cat("heartbeat")
    age_note = ""
    if heartbeat.isdigit():
        try:
            age = int(datetime.now(tz=timezone.utc).timestamp()) - int(heartbeat)
            age_note = f" (heartbeat {age}s old)"
        except (OSError, ValueError):
            pass
    return (
        f"pipeline lock: HELD (owner={_cat('owner')} run_id={_cat('run_id')} "
        f"heartbeat={heartbeat}{age_note})"
    )


def _lock_run_id(install_dir: Path | None) -> str | None:
    """The active pipeline lock's run_id, if a lock is held. Read-only."""
    if install_dir is None:
        return None
    run_id_file = install_dir / "releases" / "rollback.lock.d" / "run_id"
    try:
        if run_id_file.is_file():
            val = run_id_file.read_text(encoding="utf-8").strip()
            return val or None
    except OSError:
        pass
    return None


def _launcher_state(install_dir: Path | None) -> str:
    """``.launcher-state`` summary (§7) — key=value file, absent → defaults."""
    if install_dir is None:
        return "launcher state: none (no install dir)"
    sp = install_dir / ".launcher-state"
    if not sp.is_file():
        return f"launcher state: none at {sp}"
    values: dict[str, str] = {}
    try:
        for line in sp.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    except OSError as exc:
        return f"launcher state: unreadable at {sp} ({exc})"
    # Keys per launcher.sh read_state: last_exit, crash_count, window_start,
    # last_backoff, notified_75, last_uptime. Ignore corrupt lines (launcher
    # semantics: corrupt → defaults, never fatal).
    keys = ("last_exit", "crash_count", "window_start", "last_backoff", "last_uptime")
    rendered = " ".join(f"{k}={values.get(k, '?')}" for k in keys)
    return f"launcher: {rendered}"


def _upgrade_log_tail(install_dir: Path | None, lines: int = _UPGRADE_LOG_TAIL_LINES) -> str:
    """Bounded tail of ``<install_dir>/data/upgrade.log`` (D8/§7 observability).

    The daemonized executor's log (P2.2 write-side, Dispatch B) — read here
    when present so progress lines are observable the moment it exists.
    Bounded: at most the last 256KB is read, then the last ``lines`` lines.
    """
    if install_dir is None:
        return "upgrade.log: none (no install dir)"
    lp = install_dir / _UPGRADE_LOG_REL
    try:
        if not lp.is_file():
            return f"upgrade.log: none at {lp} (no daemonized run has logged yet)"
        size = lp.stat().st_size
        with lp.open("rb") as fh:
            if size > _UPGRADE_LOG_TAIL_CHARS:
                fh.seek(size - _UPGRADE_LOG_TAIL_CHARS)
            chunk = fh.read()
        text = chunk.decode("utf-8", errors="replace")
        tail_lines = text.splitlines()[-lines:] if lines > 0 else []
        if not tail_lines:
            return f"upgrade.log: empty at {lp}"
        body = "\n".join(f"  {ln}" for ln in tail_lines)
        return f"upgrade.log tail (last {len(tail_lines)} lines, {lp}):\n{body}"
    except OSError as exc:
        return f"upgrade.log: unreadable at {lp} ({exc})"


def _self_port(manager: "InstanceManager") -> int | None:
    """The daemon's OWN serving port — the only port these tools may probe."""
    try:
        config = getattr(manager, "config", None)
        port = getattr(getattr(config, "daemon", None), "port", None)
        return int(port) if port else None
    except (TypeError, ValueError):
        return None


def _http_get_local(port: int, path: str) -> str | None:
    """Blocking single GET on localhost (stdlib only). Never widens reach."""
    url = f"http://localhost:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


async def _probe_self(manager: "InstanceManager", path: str) -> dict[str, Any] | None:
    """Probe the SELF daemon's own endpoint (D8) — JSON dict or None.

    Offloaded to a thread + wait_for-bounded (LoopRepairer pattern); any
    failure degrades to None (probe lines render "not answering").
    """
    port = _self_port(manager)
    if port is None:
        return None
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_http_get_local, port, path),
            timeout=_PROBE_TIMEOUT_S + 1.0,
        )
    except Exception:  # probe must never raise — degrade to None
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _env_display(self_env: str | None) -> str:
    return self_env if self_env else "unresolved (dev-context)"


def _env_triple_line(self_env: str | None, install_dir: Path | None, port: int | None) -> str:
    """Parity with status.sh ``resolved env: target=... dir=... port=... db=...``."""
    triple = _ENV_TRIPLE.get(self_env or "", {"dir": None, "db": "unknown"})
    dir_display = str(install_dir) if install_dir is not None else "none (no staged install)"
    port_display = str(port) if port is not None else "?"
    return (
        f"resolved env: target={self_env or 'unresolved'} dir={dir_display} "
        f"port={port_display} db={triple['db']}"
    )


def _marker_line(self_env: str | None) -> str:
    if self_env:
        return f"env-marker: ENSEMBLE_SELF_ENV={self_env} (staged marker — D-FA2.3)"
    return (
        "env-marker: ENSEMBLE_SELF_ENV ABSENT — self-env unresolved (dev-context). "
        "Read tools answer fail-open; actor tools (Dispatch B) refuse "
        "env-marker-absent fail-closed. Only target_env omitted or \"dev\" is "
        "accepted while unresolved."
    )


def _check_target_env(
    tool_label: str, target_env: str | None, self_env: str | None
) -> str | None:
    """Self-match + enum validation for the read pair. Returns refusal or None.

    §3.2 (reviewer ruling 2026-08-22): self-match applies to READS.
    Unresolved self-env accepts only an omitted target or "dev" (the
    marker-less repo checkout IS the dev context — flagged representation).
    """
    if target_env is None:
        return None
    if target_env not in VALID_ENVS:
        return (
            f"Error: {tool_label} REFUSED — reason=invalid-target-env: "
            f"target_env must be one of {'|'.join(VALID_ENVS)} (got '{target_env}')."
        )
    if self_env is not None:
        if target_env != self_env:
            return (
                f"Error: {tool_label} REFUSED — reason=env-self-match: "
                f"target_env={target_env} but self-env={self_env}. Tools cannot "
                "target a different environment than the running daemon "
                "(self-match applies to reads — reviewer ruling 2026-08-22)."
            )
        return None
    # Marker absent: only the dev-context representation is self-consistent.
    if target_env != "dev":
        return (
            f"Error: {tool_label} REFUSED — reason=env-self-match: "
            f"target_env={target_env} but self-env=unresolved (dev-context — "
            "ENSEMBLE_SELF_ENV marker absent). Tools cannot target a different "
            "environment than the running daemon."
        )
    return None


def _terminal_outcome(journal: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
    """Derive the terminal outcome from the last journal history event.

    The P2.1 journal is NOT run-id keyed — its event vocabulary is
    commit | rollback | quarantine | sweep | sweep_rollback | halt (lib.sh
    journal schema comment). Outcome mapping is from that vocabulary only.
    """
    if not isinstance(journal, dict):
        return "unknown", None
    history = journal.get("history")
    if not isinstance(history, list) or not history:
        return "idle", None
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("event"):
            return str(entry["event"]), entry
    return "idle", None


_OUTCOME_LABELS: dict[str, str] = {
    "commit": "committed",
    "rollback": "rolled-back",
    "sweep_rollback": "rolled-back (sweep adopt — ADR-024)",
    "sweep": "swept (orphaned pre-flip txn cleared)",
    "halt": "halted-for-human",
    "quarantine": "quarantine recorded",
}


def create_upgrade_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Create the system_upgrade category's read-only tool pair.

    Args:
        manager: The :class:`InstanceManager` instance — used ONLY to read
            the daemon's own serving port (self-probe) for observability.
            No manager mutation of any kind happens in these tools.
        current_instance_id: Owning instance identifier (audit logging).
        agent_id: Caller's agent identifier (audit logging; the tool surface
            is unchanged for all agents — gating happens via tools.allow).
        agent_tag: Caller's version tag (reserved for the Dispatch-B actor
            tools; accepted now so instance.py wiring is stable).

    Returns:
        ``[release_info, upgrade_status]``.
    """

    @register_tool_category("system_upgrade")
    @tool
    async def release_info(
        target_env: Literal["dev", "demo", "live", "sandbox"] | None = None,
        section: Literal["releases", "current", "journal", "changelog", "all"] = "all",
        version: str | None = None,
    ) -> str:
        """Read-only release/upgrade pipeline snapshot for THIS daemon's environment. Use tool_help("release_info") for details."""
        try:
            refusal = _check_target_env("release_info", target_env, _self_env_marker())
            if refusal:
                return refusal

            self_env = _self_env_marker()
            install_dir = _resolve_install_dir(self_env)
            journal, journal_status = _journal_read(install_dir)
            releases = _scan_releases(install_dir, journal)
            port = _self_port(manager)

            lines: list[str] = [f"RELEASE INFO — env={_env_display(self_env)}"]
            if section == "all":
                lines.append(_marker_line(self_env))
                lines.append(_env_triple_line(self_env, install_dir, port))

            # unknown-version guard (§2.3: "Error: release_info — unknown section/version")
            if version is not None:
                known = [r.name for r in releases if r.is_release]
                if version not in known:
                    return (
                        f"Error: release_info — unknown version '{version}' "
                        f"(known releases: {', '.join(known) if known else 'none'})"
                    )

            async def _current_block(include_journal: bool = True) -> list[str]:
                block: list[str] = []
                target, note = _current_symlink(install_dir)
                if target is not None:
                    block.append(f"current={target.rsplit('/', 1)[-1]} (via releases/current → {target})")
                else:
                    block.append("current symlink: none")
                if note:
                    block.append(f"current: WARNING — {note}")
                if include_journal and isinstance(journal, dict):
                    block.append(
                        f"journal: current={journal.get('current')} "
                        f"previous={journal.get('previous')} "
                        f"in-flight={_in_flight_summary(journal)}"
                    )
                livez = await _probe_self(manager, "/livez")
                if livez is not None:
                    running_ver = livez.get("version", "(no version field)")
                    block.append(f"daemon :{port} /livez version={running_ver}")
                    # version smoke (status.sh parity) — only when a current
                    # release + its manifest binary_version are readable.
                    cur_name = target.rsplit("/", 1)[-1] if target else None
                    want = None
                    for r in releases:
                        if r.is_release and r.name == cur_name and r.manifest:
                            want = r.manifest.get("binary_version")
                    if want is not None:
                        if running_ver == want:
                            block.append(f"version smoke: OK ({running_ver} == manifest binary_version)")
                        else:
                            block.append(
                                f"version smoke MISMATCH: running={running_ver} "
                                f"manifest={want} (D2/ADR-027)"
                            )
                    readyz = await _probe_self(manager, "/readyz")
                    if readyz is not None:
                        block.append(
                            f"daemon :{port} /readyz status={readyz.get('status', '?')} "
                            f"reasons={readyz.get('reasons', [])}"
                        )
                    else:
                        block.append(f"daemon :{port} /readyz: not answering")
                else:
                    block.append(
                        f"daemon :{port if port is not None else '?'} /livez: "
                        "not answering (informational — daemon may be stopped, "
                        "or the self-probe is unavailable)"
                    )
                block.append(_launcher_state(install_dir))
                return block

            def _releases_block() -> list[str]:
                if install_dir is None:
                    return ["releases: none (no staged install dir — dev repo checkout or unresolved env)"]
                if not releases:
                    return ["releases: none (install dir not in staged mode)"]
                return ["releases:"] + [_format_release_line(r) for r in releases]

            def _journal_block(with_raw: bool) -> list[str]:
                block: list[str] = []
                jp = (
                    str(install_dir / "releases" / "state.json")
                    if install_dir is not None
                    else "(no install dir)"
                )
                if journal_status == "ok":
                    block.append(
                        f"journal: current={journal.get('current')} "  # type: ignore[union-attr]
                        f"previous={journal.get('previous')} "  # type: ignore[union-attr]
                        f"in-flight={_in_flight_summary(journal)} "
                        f"{_rollback_window_summary(journal)} "
                        f"quarantine={journal.get('quarantined') or []}"  # type: ignore[union-attr]
                    )
                    tail = _history_tail(journal, 10)
                    if tail:
                        block.append("journal history (last 10):")
                        block.extend(tail)
                    if with_raw:
                        block.append(f"journal (raw, {jp}):")
                        block.append(json.dumps(journal, indent=2, sort_keys=False))
                elif journal_status == "absent":
                    block.append(f"journal: none at {jp} (staged mode not initialized — run stage.sh)")
                elif journal_status == "no-install-dir":
                    block.append("journal: none (no staged install dir — dev repo checkout or unresolved env)")
                else:
                    block.append(
                        f"journal at {jp} is {'EMPTY' if journal_status == 'torn' else 'UNREADABLE'}"
                        " (torn write?) — treat as halt-for-human (pipeline "
                        "mutations will refuse); read tools report only"
                    )
                block.append(_lock_state(install_dir))
                return block

            def _changelog_block() -> list[str]:
                # The P2.1 manifest schema has NO changelog text field
                # (identity fields only — stage.sh manifest writer). §2.3's
                # changelog section therefore degrades to the per-release
                # manifest identity summary; no content is invented.
                block: list[str] = [
                    "changelog: (no changelog text field exists in the P2.1 "
                    "manifest schema — showing manifest identity fields per release)"
                ]
                selected = [r for r in releases if r.is_release]
                if version is not None:
                    selected = [r for r in selected if r.name == version]
                if not selected:
                    block.append("  (no releases to summarize)")
                for r in selected:
                    m = r.manifest
                    if m:
                        block.append(
                            f"  {r.name} — binary_version={m.get('binary_version', '?')} "
                            f"staged_at={m.get('staged_at', '?')} "
                            f"rollback_safe={str(m.get('rollback_safe', '?')).lower()} "
                            f"known_schema_gen={m.get('known_schema_gen', '?')} "
                            f"contains_contract_phase={str(m.get('contains_contract_phase', '?')).lower()}"
                        )
                    else:
                        block.append(f"  {r.name} — {r.manifest_error or 'no manifest'}")
                return block

            if section == "all":
                lines.extend(await _current_block(include_journal=False))
                lines.extend(_journal_block(with_raw=False))
                lines.extend(_releases_block())
                lines.extend(_changelog_block())
                lines.append(_upgrade_log_tail(install_dir))
            elif section == "current":
                lines.extend(await _current_block())
            elif section == "journal":
                lines.extend(_journal_block(with_raw=True))
                lines.append(_upgrade_log_tail(install_dir))
            elif section == "releases":
                lines.append(_env_triple_line(self_env, install_dir, port))
                lines.extend(_releases_block())
            elif section == "changelog":
                lines.extend(_changelog_block())
            else:
                return (
                    f"Error: release_info — unknown section '{section}' "
                    "(expected releases|current|journal|changelog|all)"
                )
            return "\n".join(lines)
        except Exception as exc:  # never raise — structured error string
            logger.warning(
                "release_info failed for instance=%s: %s", current_instance_id, exc
            )
            return f"Error: release_info failed: {type(exc).__name__}: {exc}"

    release_info._full_doc_ = """\
Read-only snapshot of the staged release/upgrade pipeline for THIS
daemon's own environment (P2.1 scripts/upgrade/ pipeline state).

Args:
    target_env: Must equal the running daemon's own environment
        (dev|demo|live|sandbox). Omit to target self. Cross-env reads are
        REFUSED (env-self-match — reads are covered by the self-match
        rule, reviewer ruling 2026-08-22). When the ENSEMBLE_SELF_ENV
        marker is absent (dev repo checkout), only an omitted target_env
        or "dev" is accepted.
    section: One of:
        * "all" (default) — current + journal + releases + changelog +
          upgrade.log tail.
        * "current" — current symlink, journal current/previous/in-flight,
          /livez + /readyz self-probes, version smoke vs the current
          manifest's binary_version, launcher state.
        * "journal" — full journal state incl. the raw releases/state.json,
          history tail, pipeline lock, cooldown/rollback-window counters,
          upgrade.log tail.
        * "releases" — releases/ inventory with per-release manifest
          summary (rollback_safe, known_schema_gen, binary_version,
          staged_at) + quarantine/pinned annotations; protocol artifacts
          (rollback.lock.d, .staging.*) are labelled, not listed as
          releases.
        * "changelog" — per-release manifest identity summary (the P2.1
          manifest has no changelog text field; nothing is invented).
    version: Optional specific release name (e.g. "1.2.3") — filters the
        changelog section / adds a release-detail focus. Unknown version →
        "Error: release_info — unknown version ...".

Returns:
    Line-oriented, LLM-friendly text (status.sh-parity fields: resolved
    env triple, journal state, releases inventory, current symlink,
    pipeline lock, /livez version + version smoke). Refusals are
    "Error: release_info REFUSED — reason=..." strings. The tool NEVER
    mutates anything: no signals, no locks, no journal writes, no
    non-self network access.
"""

    @register_tool_category("system_upgrade")
    @tool
    async def upgrade_status(
        target_env: Literal["dev", "demo", "live", "sandbox"] | None = None,
        run_id: str | None = None,
        tail: int = 20,
    ) -> str:
        """Poll a pipeline run for THIS daemon's environment: in-flight phase, journal tail, terminal outcome. Use tool_help("upgrade_status") for details."""
        try:
            refusal = _check_target_env("upgrade_status", target_env, _self_env_marker())
            if refusal:
                return refusal

            self_env = _self_env_marker()
            install_dir = _resolve_install_dir(self_env)
            journal, journal_status = _journal_read(install_dir)
            port = _self_port(manager)
            tail = max(1, min(int(tail), 100))
            lock_run_id = _lock_run_id(install_dir)

            lines: list[str] = [f"UPGRADE STATUS — env={_env_display(self_env)}"]

            # run_id correlation (§2.4). NOTE: the P2.1 journal schema has
            # NO run_id field (in_flight = kind/target/started_at/flipped/
            # owner_pid; history = ts/event/detail). Today the only run-id
            # source is the pipeline lock directory. Run-scoped journal
            # pending-ops arrive with the Dispatch-B write side; until then
            # a run_id that matches nothing is an error + latest-state
            # fallback rather than a fabricated run view.
            run_note = None
            if run_id is not None and (lock_run_id is None or lock_run_id != run_id):
                run_note = (
                    f"Error: upgrade_status — unknown run_id={run_id}: no pipeline "
                    "lock or journal record matches this run id (the P2.1 journal "
                    "is not run-id keyed; run-scoped pending-ops arrive with the "
                    "actor tools' journal write side). Latest self-env state follows:"
                )

            in_flight = journal.get("in_flight") if isinstance(journal, dict) else None

            if isinstance(in_flight, dict):
                started = _parse_iso_utc(in_flight.get("started_at"))
                if started is not None:
                    elapsed = int((datetime.now(tz=timezone.utc) - started).total_seconds())
                    elapsed_txt = f"{elapsed}s"
                else:
                    elapsed_txt = "unknown (unparseable started_at — fail-closed display)"
                flipped = str(in_flight.get("flipped", "?")).lower() == "true"
                phase = "post-flip (gating/soak)" if flipped else "pre-flip"
                lines.append(
                    f"txn=IN-FLIGHT kind={in_flight.get('kind', '?')} "
                    f"target={in_flight.get('target', '?')} "
                    f"started={in_flight.get('started_at', '?')} elapsed={elapsed_txt} "
                    f"owner_pid={in_flight.get('owner_pid', '?')} phase={phase}"
                )
                lines.append(
                    f"run: {lock_run_id or 'no run-id recorded (P2.1 journal is not run-id keyed)'}"
                )
                lines.append(_lock_state(install_dir))
                lines.append(_rollback_window_summary(journal))
                hist = _history_tail(journal, tail)
                if hist:
                    lines.append(f"journal tail (last {len(hist)}):")
                    lines.extend(hist)
                if run_id is not None and lock_run_id is not None and run_id == lock_run_id:
                    lines.append(f"run_id={run_id} matches the active pipeline lock")
                lines.append(_upgrade_log_tail(install_dir, lines=min(tail, 50)))
                lines.append(
                    "next: terminal outcome lands in journal history as "
                    "commit/rollback/halt; poll again (same run_id) or read "
                    "release_info(section=journal)"
                )
            else:
                event_key, last_entry = _terminal_outcome(journal)
                if journal_status == "ok" and event_key != "idle":
                    lines.append("TERMINAL")
                    if last_entry is not None:
                        lines.append(
                            f"outcome={_OUTCOME_LABELS.get(event_key, event_key)} "
                            f"last event: {last_entry.get('ts', '?')} "
                            f"{last_entry.get('event', '?')} — {last_entry.get('detail', '')}"
                        )
                    else:
                        lines.append(f"outcome={_OUTCOME_LABELS.get(event_key, event_key)}")
                    if isinstance(journal, dict):
                        lines.append(
                            f"current={journal.get('current')} previous={journal.get('previous')} "
                            f"{_rollback_window_summary(journal)}"
                        )
                        quar = journal.get("quarantined") or []
                        if quar:
                            lines.append(f"quarantine={quar}")
                elif journal_status == "ok":
                    lines.append("IDLE — no in-flight txn and no journal history events")
                    if isinstance(journal, dict):
                        lines.append(
                            f"current={journal.get('current')} previous={journal.get('previous')} "
                            f"{_rollback_window_summary(journal)}"
                        )
                elif journal_status == "absent":
                    lines.append(
                        "journal: none (staged mode not initialized — no runs recorded; "
                        "run stage.sh on a demo/sandbox install first)"
                    )
                elif journal_status == "no-install-dir":
                    lines.append(
                        "journal: none (no staged install dir — dev repo checkout or "
                        "unresolved env; no pipeline runs to poll)"
                    )
                else:
                    lines.append(
                        "journal is EMPTY/UNREADABLE (torn write?) — treat as "
                        "halt-for-human (pipeline mutations will refuse); reads report only"
                    )
                lines.append(_lock_state(install_dir))
                livez = await _probe_self(manager, "/livez")
                if livez is not None:
                    lines.append(f"daemon :{port} /livez version={livez.get('version', '?')}")
                    readyz = await _probe_self(manager, "/readyz")
                    lines.append(
                        f"daemon :{port} /readyz status={readyz.get('status', 'not answering') if readyz else 'not answering'}"
                    )
                else:
                    lines.append(
                        f"daemon :{port if port is not None else '?'} /livez: not answering"
                    )

            if run_note is not None:
                return run_note + "\n" + "\n".join(lines)
            return "\n".join(lines)
        except Exception as exc:  # never raise — structured error string
            logger.warning(
                "upgrade_status failed for instance=%s: %s", current_instance_id, exc
            )
            return f"Error: upgrade_status failed: {type(exc).__name__}: {exc}"

    upgrade_status._full_doc_ = """\
Poll the staged release/upgrade pipeline for THIS daemon's own
environment: the in-flight txn (kind, target, phase pre-flip/post-flip,
elapsed), journal history tail, pipeline lock, rollback-window/cooldown
counters, and — when no txn is in flight — the terminal outcome derived
from the last journal history event (committed / rolled-back / swept /
halted-for-human / quarantine / idle).

Args:
    target_env: Must equal the running daemon's own environment
        (dev|demo|live|sandbox). Omit to target self. Cross-env reads are
        REFUSED (env-self-match — reads included, reviewer ruling
        2026-08-22). Marker absent → only omitted or "dev" accepted.
    run_id: Optional pipeline run identifier. NOTE: the P2.1 journal
        schema is NOT run-id keyed (in_flight = kind/target/started_at/
        flipped/owner_pid; history = ts/event/detail) — today the only
        run-id source is the active pipeline lock directory
        (releases/rollback.lock.d/run_id). A run_id matching nothing
        returns "Error: upgrade_status — unknown run_id=..." plus the
        latest self-env state as fallback. Run-scoped journal correlation
        (the cross-death join key) arrives with the actor tools' journal
        write side (P2.2 Dispatch B). Default: latest state for self-env.
    tail: Journal history lines to include (default 20, clamped 1..100).

Returns:
    Line-oriented, LLM-friendly text. Refusals are
    "Error: upgrade_status REFUSED — reason=..." strings. The tool NEVER
    mutates: no signals, no locks, no journal writes; the optional
    /livez + /readyz probes hit ONLY the daemon's own serving port.
"""

    return [release_info, upgrade_status]
