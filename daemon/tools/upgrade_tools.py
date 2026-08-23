"""System Upgrade tools — release/upgrade observability + actor tools (P2.2).

Category ``system_upgrade`` per the self-restart/upgrade Phase-2 design
(``.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md``).

* ``release_info``  — §2.3: releases / current / journal / changelog views
  (read-only, Dispatch A).
* ``upgrade_status`` — §2.4: run-scoped pipeline poller (journal + lock
  tail; run_id correlation via the lock dir + the Dispatch-B pending_op /
  in_flight.run_id records).
* ``system_restart`` — §2.2 + D-FA1.4: arm → return → poll. LIVE refused
  outright (A2). demo/dev/sandbox free (journaled + lock-protected).
* ``system_upgrade`` — §2.1 + §4: dry_run-default-true preflight (live:
  issues the action-binding nonce) / armed promote via daemonized
  ``promote.sh``. LIVE armed = 3-factor gate (param + user-origin marker
  + nonce content match) BEFORE any live action.

Read pair discipline (hard constraint, live-safe — unchanged from
Dispatch A): the read tools perform NO mutations of any kind. The ACTOR
tools mutate EXCLUSIVELY through the P2.1 pipeline surfaces: journal
writes go through ``upgrade_journal`` (atomic temp+rename, ADR-034
splice-discipline preserved), execution goes through daemonized
``restart.sh`` / ``promote.sh`` (SINGLE-TERM stop contract — NEVER a raw
kill), serialization through the ``rollback.lock.d`` mkdir protocol.

Journal discipline (ADR-034): ``upgrade_journal`` never writes or
splices ``releases/state.json`` textually — the document round-trips
structurally (json.loads → dict → json.dumps), preserving lib.sh's ≥2
occurrence escape tolerance for field divergence by construction.

Self-match rule (§3.2, reviewer ruling 2026-08-22): ``target_env`` must
equal the daemon's own env (staged ``ENSEMBLE_SELF_ENV`` marker,
D-FA2.3 — PORT-derivation fallback REJECTED). Cross-env reads AND
actions are refused structurally with ``env-self-match``; a
dev/demo/sandbox daemon can NEVER address live, whatever parameters the
LLM passes. Marker absent → read tools STILL answer (fail-open for
reads only); ACTOR tools refuse ``env-marker-absent`` fail-closed
(S-31).
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
from . import upgrade_journal as uj
from .upgrade_journal import (
    JournalTorn,
    PendingAction,
    PendingOp,
    USER_ORIGIN_SOURCES,
    iso_plus,
    mint_nonce,
    mint_run_id,
    nonce_grouped,
    lock_acquire as journal_lock_acquire,
    lock_release as journal_lock_release,
    lock_run_id as journal_lock_run_id,
    now_iso,
    parse_iso_utc,
    spawn_executor,  # noqa: F401  # test patch seam (no_spawn)
)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "System Upgrade"
CATEGORY_DOC = """\
Visibility + conversational control of the staged release/upgrade
pipeline (P2.1 pipeline, P2.2 tool surface).

- `release_info` — current release, staged releases + manifests, journal
  state (in-flight txn, rollback cap/cooldown, quarantine), pipeline lock,
  launcher state, and the daemon's own /livez + /readyz probes.
- `upgrade_status` — poll a pipeline run by run_id: in-flight phase,
  journal history tail, terminal outcome (committed / rolled-back /
  halted).
- `system_restart` — schedule an intentional restart (arm → return →
  poll; SINGLE-TERM stop + launcher re-exec, never a raw kill). LIVE is
  refused outright this initiative.
- `system_upgrade` — arm the promote pipeline (dry_run defaults TRUE;
  live requires the 3-factor confirmation: user_confirmed + user-origin
  turn + nonce echoed by the user).

All four tools target ONLY the running daemon's own environment
(``target_env`` must equal the staged ENSEMBLE_SELF_ENV marker);
cross-env calls are refused. Reads never mutate; actor tools mutate only
through the journal (atomic writes) and the daemonized pipeline scripts.
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
#     names (factory-created, not import-time registered).             [DONE A+B]
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
#     fragment (confirmation protocol) in tools_note.md.               [DONE B]
#  9. Actor tools system_restart / system_upgrade bodies + write-side
#     journal helpers (daemon/tools/upgrade_journal.py) + daemonized
#     executor + nonce / USER_ORIGIN_SOURCES gate + post-turn trigger. [DONE B]
# 10. Docs: CATEGORY_DOC above; docs/ has no per-tool catalog to update
#     (tool_help reads _full_doc_).                                     [DONE A]
# ───────────────────────────────────────


VALID_ENVS: tuple[str, ...] = ("dev", "demo", "live", "sandbox")

# Env DB-name table mirroring scripts/upgrade/lib.sh resolve_env (display
# parity with status.sh "resolved env:" line). Install dirs are
# intentionally NOT table-driven: dev has NO staged install dir, sandbox
# derives it from the frozen binary location, demo/live resolve via
# _resolve_install_dir — the dir display always uses that resolver's
# answer, never a table guess. The probe port likewise comes from the
# daemon's OWN serving port (manager config), never a table entry.
_ENV_DB: dict[str, str] = {
    "dev": "ensemble_dev",
    "demo": "ensemble_demo",
    "live": "ensemble_prod",
    "sandbox": "ensemble_sandbox",
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


def _journal_read(install_dir: Path | None) -> tuple[dict[str, Any] | None, str]:
    """Torn-safe READ of ``<install_dir>/releases/state.json``.

    Returns ``(data, status)`` with status in ``{"ok", "absent", "torn",
    "no-install-dir", "unreadable"}``: ok → parsed dict; absent → no
    journal file yet; no-install-dir → no staged install resolves (data
    is None without touching the filesystem); unreadable → OSError on
    the read (logged); torn → empty file, unparseable JSON, or a
    non-object JSON value (treat as untrusted — pipeline mutations
    refuse; reads just report it). Semantics mirror lib.sh
    ``journal_read``. NEVER writes, NEVER splices, NEVER locks.
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


def _launcher_state(install_dir: Path | None) -> str:
    """``.launcher-state`` summary (§7) — key=value file, absent → defaults."""
    if install_dir is None:
        return "launcher state: none (no install dir)"
    sp = install_dir / ".launcher-state"
    if not sp.is_file():
        return f"launcher state: none at {sp}"
    values = _launcher_state_values(install_dir)
    # Keys per launcher.sh read_state: last_exit, crash_count, window_start,
    # last_backoff, notified_75, last_uptime. notified_75 is parsed but
    # silently dropped from this render (it only gates launcher.sh's own
    # one-shot tempfail-75 desktop notification — no display value here;
    # behavior unchanged). Corrupt/absent lines → '?' defaults (launcher
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
    """Blocking single GET on localhost (stdlib only). Never widens reach.
    Deliberately proxy-bypassed (``ProxyHandler({})``): this is a SELF-probe
    and must hit the daemon directly — an ambient ``http_proxy`` would
    otherwise route it through a foreign proxy (and a bogus/unreachable
    proxy would fail a perfectly healthy daemon)."""
    url = f"http://localhost:{port}{path}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=_PROBE_TIMEOUT_S) as resp:
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
    db = _ENV_DB.get(self_env or "", "unknown")
    dir_display = str(install_dir) if install_dir is not None else "none (no staged install)"
    port_display = str(port) if port is not None else "?"
    return (
        f"resolved env: target={self_env or 'unresolved'} dir={dir_display} "
        f"port={port_display} db={db}"
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
    "restart": "restarted (intentional)",
    "nonce_consumed": "live-confirmation nonce consumed",
}


# ═══════════════════════════════════════
# Actor-tool helpers (P2.2 Dispatch B — system_restart / system_upgrade)
# ═══════════════════════════════════════


def _refusal(label: str, reason: str, message: str) -> str:
    """Structured refusal string — D-FA2.2: ``Error:`` prefix, distinct
    machine-readable ``reason=<token>`` per taxonomy entry."""
    return f"Error: {label} REFUSED — reason={reason}: {message}"


def _actor_env_gate(
    label: str, target_env: str | None
) -> tuple[str | None, Path | None, str | None]:
    """Actor-tool env checks in the D-FA2.4 order. Returns
    ``(self_env, install_dir, refusal)``.

    1. enum validation (invalid-target-env)
    2. staged-marker resolution — absent → env-marker-absent (S-31:
       ACTOR tools fail-closed; the read pair answers fail-open)
    3. self-match (env-self-match) — BEFORE any live-gate logic, so a
       cross-env attempt can never reach the live branch.
    """
    if target_env is None or target_env not in VALID_ENVS:
        return None, None, _refusal(
            label,
            "invalid-target-env",
            f"target_env must be one of {'|'.join(VALID_ENVS)} (got '{target_env}').",
        )
    self_env = _self_env_marker()
    if self_env is None:
        return None, None, _refusal(
            label,
            "env-marker-absent",
            "ENSEMBLE_SELF_ENV marker absent/invalid — the daemon's own env is "
            "unresolved, so actor tools refuse fail-closed (S-31/D-FA2.3; read "
            "tools still answer). PORT-derivation is deliberately NOT attempted.",
        )
    if target_env != self_env:
        return None, None, _refusal(
            label,
            "env-self-match",
            f"target_env={target_env} but self-env={self_env}. Tools cannot "
            "target a different environment than the running daemon "
            "(hard constraint §3).",
        )
    return self_env, _resolve_install_dir(self_env), None


def _launcher_state_values(install_dir: Path | None) -> dict[str, str]:
    """Parse ``.launcher-state`` (key=value; corrupt lines ignored — launcher
    semantics: corrupt → defaults, never fatal)."""
    if install_dir is None:
        return {}
    sp = install_dir / ".launcher-state"
    try:
        if not sp.is_file():
            return {}
        values: dict[str, str] = {}
        for line in sp.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        return values
    except OSError:
        return {}


_LAUNCHER_BUDGET_MAX_CRASHES = 5     # launcher.sh BUDGET_MAX_CRASHES
_LAUNCHER_BUDGET_WINDOW_S = 600      # launcher.sh BUDGET_WINDOW_S
_LAUNCHER_CLEAN_EXITS = {"0", "75", "78"}


def _burst_abort_latched(install_dir: Path | None) -> bool:
    """Is the launcher in the burst-abort hold (exit-1 latch)? Signature
    (launcher.sh budget_tick abort branch): crash_count > 5 within the
    fresh 600s window AND last_exit is a crash-class code (not 0/75/78).
    A restart would zero the uptime and reset the burst budget — masking
    the failure (§5 interlock 6) — so it is refused."""
    values = _launcher_state_values(install_dir)
    count = values.get("crash_count", "")
    last_exit = values.get("last_exit", "")
    window = values.get("window_start", "")
    if not count.isdigit() or not last_exit.lstrip("-").isdigit():
        return False
    if int(count) <= _LAUNCHER_BUDGET_MAX_CRASHES:
        return False
    if last_exit in _LAUNCHER_CLEAN_EXITS:
        return False
    if window.isdigit() and int(window) > 0:
        age = int(datetime.now(tz=timezone.utc).timestamp()) - int(window)
        if age > _LAUNCHER_BUDGET_WINDOW_S:
            return False  # window aged out — no longer the latch hold
    return True


def _rollback_count_24h(journal: dict[str, Any] | None) -> int | None:
    """Sliding-window rollback count — mirror of lib.sh
    ``journal_rollback_count_24h`` (window anchored at the LAST rollback;
    window_start >24h old → 0; empty/null window → 0; unparseable non-null
    window keeps the stored count — the conservative direction)."""
    if not isinstance(journal, dict):
        return None
    counts = journal.get("rollback_window_count")
    if not isinstance(counts, dict):
        return None
    raw = counts.get("24h")
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    wstart = counts.get("window_start")
    if not isinstance(wstart, str) or not wstart or wstart == "null":
        return 0
    started = parse_iso_utc(wstart)
    if started is not None:
        age = (datetime.now(tz=timezone.utc) - started).total_seconds()
        if age >= 86400:
            return 0
    return count


def _version_sort_key(tag: str) -> tuple[int, tuple]:
    """Sort key for release tags — semantic (numeric dot-segments) where
    parseable, lexical fallback otherwise (N6, P2.3 B3.5: the old
    ``sorted()[-1]`` lexical pick made 1.2.9 beat 1.2.10).

    Fully-numeric dot tags (``1.2.10``) sort as a tuple of ints AFTER all
    non-numeric tags (rank 1 vs 0), so a stray ``v1.2.3``/``1.2.3-rc1``
    directory can never outrank a plain numeric release; different
    lengths compare prefix-first (``1.2`` < ``1.2.0`` < ``1.2.10``).
    Mixed/non-numeric tags fall back to plain lexical order within rank 0.
    """
    parts = tag.split(".")
    if parts and all(p.isdigit() for p in parts):
        return (1, tuple(int(p) for p in parts))
    return (0, (tag,))


def _cooldown_state(journal: dict[str, Any] | None) -> str:
    """'clear' | 'active-until=<iso>' — FAIL CLOSED (lib.sh M3): a
    present-but-unparseable cooldown_until counts as ACTIVE; only an
    explicit null/absent field is clear."""
    if not isinstance(journal, dict):
        return "unknown"
    until = journal.get("cooldown_until")
    if until is None or until == "null":
        return "clear"
    ts = parse_iso_utc(until)
    if ts is None:
        return "active-until=<unparseable> (fail-closed, ADR-005 anti-flapping)"
    if datetime.now(tz=timezone.utc) < ts:
        return f"active-until={until}"
    return "clear"


def _target_release_state(
    install_dir: Path | None, version: str
) -> tuple[str, dict[str, Any]]:
    """(status, manifest) with status in
    {"ok", "target-not-staged", "manifest-unsafe", "target-quarantined"}
    (manifest empty unless ok/partial)."""
    if install_dir is None:
        return "target-not-staged", {}
    rel_dir = install_dir / "releases" / version
    if not rel_dir.is_dir():
        return "target-not-staged", {}
    manifest: dict[str, Any] = {}
    mp = rel_dir / "manifest.json"
    try:
        loaded = json.loads(mp.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            manifest = loaded
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if not manifest:
        return "manifest-unsafe", {}
    rb = manifest.get("rollback_safe")
    if rb is not True and str(rb).strip().lower() != "true":
        return "manifest-unsafe", manifest
    return "ok", manifest


def _in_flight_state(journal: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(journal, dict) and isinstance(journal.get("in_flight"), dict):
        return journal["in_flight"]
    return None


def _resolve_scripts_dir(install_dir: Path | None) -> Path | None:
    """Locate the P2.1 pipeline scripts (``lib.sh`` + payloads). Order:
    (1) explicit ``ENSEMBLE_UPGRADE_SCRIPTS_DIR`` (drills/ops); (2) the
    repo checkout this module lives in (source-mode daemons — the frozen
    binary does not carry scripts/); (3) ``<install_dir>/scripts/upgrade``
    (if a future release stages them). Armed execution without a resolvable
    scripts dir refuses ``executor-scripts-unavailable`` (fail-closed)."""
    candidates: list[Path] = []
    override = os.environ.get("ENSEMBLE_UPGRADE_SCRIPTS_DIR", "").strip()
    if override:
        candidates.append(Path(override))
    try:
        candidates.append(Path(__file__).resolve().parents[2] / "scripts" / "upgrade")
    except (IndexError, OSError):
        pass
    if install_dir is not None:
        candidates.append(install_dir / "scripts" / "upgrade")
    for cand in candidates:
        try:
            if (cand / "lib.sh").is_file():
                return cand
        except OSError:
            continue
    return None


async def _busy_advisory(manager: "InstanceManager", instance_id: str) -> str:
    """D-FA5.2: ``has_instance_busy`` reported in every actor result but
    NEVER gates — an env wedged busy would deadlock upgrades forever;
    checkpoint resume is the correctness net."""
    try:
        repo = getattr(manager, "_task_repo", None)
        if repo is None or not hasattr(repo, "has_instance_busy"):
            return "busy-check=unavailable (task repository not wired)"
        busy = await asyncio.to_thread(repo.has_instance_busy, instance_id)
        if busy:
            return (
                "busy-check=BUSY (live tasks exist — restart proceeds; in-flight "
                "work resumes from checkpoints, D-FA5.2)"
            )
        return "busy-check=idle"
    except Exception as exc:  # advisory only — never blocks
        return f"busy-check=unavailable ({type(exc).__name__})"


def _set_execution_marker(manager: "InstanceManager", instance_id: str, spec: dict[str, Any]) -> bool:
    """Arm the in-memory post-turn trigger marker (D-FA1.4). The DURABLE
    state is the journal pending_op (written before the tool returns);
    this marker only tells the post-graph callback to fire the daemonized
    executor at exact turn-end. Best-effort: a lost marker degrades to the
    fallback (bounded waiter / boot sweep) — latency, never silent loss."""
    setter = getattr(manager, "set_pending_system_execution", None)
    if not callable(setter):
        logger.warning(
            "upgrade_tools: manager lacks set_pending_system_execution — "
            "post-turn trigger unavailable (fallback: manual restart.sh / boot sweep)"
        )
        return False
    setter(instance_id, spec)
    return True


def _live_gate_summary(label: str, factor_failures: list[str]) -> str:
    return _refusal(
        label,
        factor_failures[0],
        "; ".join(factor_failures) if factor_failures else "3-factor gate failed",
    )



def create_upgrade_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Create the system_upgrade category's 4-tool surface: the read pair
    (``release_info``, ``upgrade_status``) + the actor pair
    (``system_restart``, ``system_upgrade``).

    Args:
        manager: The :class:`InstanceManager` instance. The read pair uses
            it only for the daemon's own serving port (self-probe); the
            actor tools additionally rely on three manager interactions —
            ``set_pending_system_execution`` (in-memory post-turn trigger
            marker, best-effort), ``_task_repo.has_instance_busy``
            (advisory busy check, never gates), and the
            ``_user_origin_windows`` dict (live 3-factor gate reads).
            No durable manager state is mutated; journal + lock on disk
            carry the durability.
        current_instance_id: Owning instance identifier (audit logging).
        agent_id: Caller's agent identifier (audit logging; the tool surface
            is unchanged for all agents — gating happens via tools.allow).
        agent_tag: Caller's version tag. Accepted but unused by the tool
            bodies — kept so instance.py wiring stays stable across agent
            versions (removal is tracked in the P2.3 ledger).

    Returns:
        ``[release_info, upgrade_status, system_restart, system_upgrade]``.
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
            self_env = _self_env_marker()  # resolved ONCE per invocation
            refusal = _check_target_env("release_info", target_env, self_env)
            if refusal:
                return refusal

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
            self_env = _self_env_marker()  # resolved ONCE per invocation
            refusal = _check_target_env("upgrade_status", target_env, self_env)
            if refusal:
                return refusal

            install_dir = _resolve_install_dir(self_env)
            journal, journal_status = _journal_read(install_dir)
            port = _self_port(manager)
            tail = max(1, min(int(tail), 100))
            lock_run_id = journal_lock_run_id(install_dir)

            lines: list[str] = [f"UPGRADE STATUS — env={_env_display(self_env)}"]

            # run_id correlation (§2.4 / D-FA1.1 — run_id is the cross-death
            # join key). Sources: the active pipeline lock directory, the
            # Dispatch-B pending_op record, and in_flight.run_id (tool-armed
            # restart txns carry it). A run_id that matches nothing is an
            # error + latest-state fallback rather than a fabricated run view.
            pending_op_json = (
                journal.get("pending_op") if isinstance(journal, dict) else None
            )
            pending_run_id = (
                pending_op_json.get("run_id")
                if isinstance(pending_op_json, dict)
                else None
            )
            in_flight_run_id = (
                journal.get("in_flight", {}).get("run_id")
                if isinstance(journal, dict) and isinstance(journal.get("in_flight"), dict)
                else None
            )
            run_note = None
            if run_id is not None and run_id not in {
                lock_run_id,
                pending_run_id,
                in_flight_run_id,
            }:
                # Terminated-run UX (review nit #8): a finished run's
                # terminal event names its run_id in the history detail —
                # the run IS known, just no longer active. Soften the note
                # instead of crying "unknown run_id" at a completed run.
                history = journal.get("history") if isinstance(journal, dict) else None
                run_in_history = isinstance(history, list) and any(
                    isinstance(e, dict) and run_id in str(e.get("detail", ""))
                    for e in history
                )
                if run_in_history:
                    run_note = (
                        f"run_id={run_id} not active — terminal events in "
                        "tail below (the run completed; its journal record "
                        "is historical)"
                    )
                else:
                    run_note = (
                        f"Error: upgrade_status — unknown run_id={run_id}: no pipeline "
                        "lock, pending-op, or journal record matches this run id "
                        "(latest self-env state follows)"
                    )

            in_flight = journal.get("in_flight") if isinstance(journal, dict) else None

            if isinstance(in_flight, dict):
                started = parse_iso_utc(in_flight.get("started_at"))
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
                    f"run: {lock_run_id or in_flight_run_id or 'no run-id recorded'}"
                )
                if isinstance(pending_op_json, dict):
                    lines.append(
                        f"pending-op: run_id={pending_run_id} kind={pending_op_json.get('kind')} "
                        f"target={pending_op_json.get('target')} armed_at={pending_op_json.get('armed_at')} "
                        f"trigger={pending_op_json.get('trigger')} owner_kind={pending_op_json.get('owner_kind')}"
                    )
                lines.append(_lock_state(install_dir))
                lines.append(_rollback_window_summary(journal))
                hist = _history_tail(journal, tail)
                if hist:
                    lines.append(f"journal tail (last {len(hist)}):")
                    lines.extend(hist)
                if run_id is not None and run_id in {lock_run_id, in_flight_run_id}:
                    lines.append(f"run_id={run_id} matches the active pipeline run")
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
    run_id: Optional pipeline run identifier (the cross-death join key
        returned by system_restart / system_upgrade). Correlated against
        the active pipeline lock directory (releases/rollback.lock.d/
        run_id), the journal pending_op record, and in_flight.run_id.
        A run_id matching nothing returns "Error: upgrade_status —
        unknown run_id=..." plus the latest self-env state as fallback.
        Default: latest state for self-env.
    tail: Journal history lines to include (default 20, clamped 1..100).

Returns:
    Line-oriented, LLM-friendly text. Refusals are
    "Error: upgrade_status REFUSED — reason=..." strings. The tool NEVER
    mutates: no signals, no locks, no journal writes; the optional
    /livez + /readyz probes hit ONLY the daemon's own serving port.
"""

    # ═══════════════════ ACTOR TOOLS (P2.2 Dispatch B) ═════════════════════

    @register_tool_category("system_upgrade")
    @tool
    async def system_restart(
        target_env: str,
        reason: str,
        user_confirmed: bool = False,
        mode: str = "graceful-now",
        nonce: str | None = None,
        dry_run: bool = True,
    ) -> str:
        """Schedule an intentional restart of THIS daemon's environment (end-of-turn, health-gated). Use tool_help("system_restart") for details."""
        try:
            label = "RESTART"
            # D-FA2.4 order: enum checks first (mode + target_env)…
            if mode != "graceful-now":
                return _refusal(
                    label, "unknown-mode", "mode must be graceful-now."
                )
            self_env, install_dir, refusal = _actor_env_gate(label, target_env)
            if refusal:
                return refusal
            # …then the live outright refusal (A2 — BEFORE any gate logic;
            # no override, no dry-run exception).
            if self_env == "live":
                return (
                    "Error: RESTART REFUSED — reason=live-restart-refused: live "
                    "restart is USER-GATED this initiative (A2/§3.1) — the tool "
                    "refuses outright, no gate, no override. Use the manual "
                    "procedure: bash scripts/stop-ensemble.sh <install-dir> then "
                    "start launcher.sh (see the P2.3 runbook)."
                )

            # Pipeline preconditions (armed + dry-run reporting). A torn or
            # absent journal is DISTINCT from no-install-dir (review nit
            # #11): the install dir resolves but the journal cannot be
            # trusted → its own refusal token, never a misleading
            # no-staged-install.
            journal: dict[str, Any] | None = None
            journal_status = "unreadable"
            if install_dir is not None:
                try:
                    uj.reconcile_pending_op(install_dir)
                except Exception as exc:  # best-effort sweep — never gates
                    logger.warning(
                        "system_restart: reconcile_pending_op failed for "
                        "%s: %s — continuing on journal read status",
                        install_dir, exc,
                    )
                journal, journal_status = _journal_read(install_dir)
            if install_dir is None:
                return _refusal(
                    label,
                    "no-staged-install",
                    "no staged install dir resolves for this env (dev repo "
                    "checkout / unresolved) — a restart acts on a staged install.",
                )
            if journal is None:
                return _refusal(
                    label,
                    "journal-unavailable",
                    f"journal at {install_dir / 'releases' / 'state.json'} is "
                    f"{journal_status} (torn/absent) — halt-for-human: repair "
                    "the journal before any pipeline action (reads still answer).",
                )
            pending = uj.read_pending_op(install_dir)
            held_run = journal_lock_run_id(install_dir)
            in_flight = _in_flight_state(journal)
            if in_flight is not None:
                kind = in_flight.get("kind", "?")
                inf_run = in_flight.get("run_id")
                run_frag = f" run_id={inf_run}" if inf_run else ""
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"in-flight txn kind={kind} target={in_flight.get('target', '?')} "
                    f"started_at={in_flight.get('started_at', '?')}{run_frag} "
                    "(run upgrade_status to follow it; a restart txn is completed by "
                    "restart.sh, a promote txn by promote.sh)",
                )
            if pending is not None:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"pending op run_id={pending.run_id} kind={pending.kind} "
                    f"armed_at={pending.armed_at} is armed (lock run_id={held_run or 'none'}); "
                    "poll upgrade_status(run_id=...) for its outcome",
                )
            if held_run is not None:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"pipeline lock held (run_id={held_run}) — another pipeline "
                    "action owns it; retry via upgrade_status",
                )
            if _burst_abort_latched(install_dir):
                return _refusal(
                    label,
                    "restart-under-burst-abort",
                    # NIT-4 (P2.2 tidy cycle-3, closed P2.3 B3.5): render
                    # via the curated _launcher_state helper — the same
                    # display path release_info/restart-preview use —
                    # instead of a raw single-field dict get.
                    "daemon is in burst-abort hold (launcher exit-1 latch; "
                    f"{_launcher_state(install_dir)}); restart would mask "
                    "the failure. Resolve the burst condition first.",
                )

            busy = await _busy_advisory(manager, current_instance_id)

            if dry_run:
                cur_target, _note = _current_symlink(install_dir)
                lines = [
                    f"RESTART PREVIEW (dry-run) — env={self_env} mode=graceful-now",
                    f"reason: {reason}",
                    f"current release: {cur_target.rsplit('/', 1)[-1] if cur_target else 'none (dev/unstaged)'}",
                    _launcher_state(install_dir),
                    busy,
                    "PLAN: arm journal pending-op (kind=restart) → this turn "
                    "completes → daemonized restart.sh (SINGLE-TERM stop via "
                    "stop-ensemble.sh — NEVER a raw kill) → detached launcher "
                    "re-exec → /livez gate ≤60s → journal 'restart' event",
                    "expected downtime 15-90s; in-flight turns freeze at node "
                    "boundaries and resume (checkpoint resume)",
                    "NO mutation happened (dry-run). Call again with "
                    "dry_run=false to schedule the restart.",
                ]
                return "\n".join(lines)

            # ARM (§6.3: arm → return → poll; the tool returns BEFORE any
            # stop signal — the executor fires at exact turn-end).
            scripts_dir = _resolve_scripts_dir(install_dir)
            if scripts_dir is None or not (scripts_dir / "restart.sh").is_file():
                return _refusal(
                    label,
                    "executor-scripts-unavailable",
                    "cannot resolve scripts/upgrade/restart.sh (set "
                    "ENSEMBLE_UPGRADE_SCRIPTS_DIR or run from a source checkout)",
                )
            run_id = mint_run_id()
            acquired, busy_run = journal_lock_acquire(install_dir, run_id)
            if not acquired:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"per-env lock held (run_id={busy_run or held_run or '?'}) — "
                    "retry via upgrade_status",
                )
            try:
                uj.journal_init(install_dir)
                uj.ensure_extensions(install_dir)
                cur_target, _n2 = _current_symlink(install_dir)
                cur_ver = cur_target.rsplit("/", 1)[-1] if cur_target else None
                op = PendingOp(
                    run_id=run_id,
                    kind="restart",
                    env=self_env,
                    target=cur_ver,
                    mode="graceful-now",
                    reason=reason,
                    armed_by_instance=current_instance_id,
                    owner_pid=os.getpid(),
                    owner_kind="tool-arm",
                    expires_at=iso_plus(now_iso(), uj.PENDING_OP_EXPIRE_RESTART_S),
                    confirmed_by_human=False,
                    confirmed_source=None,
                )
                # The restart txn (D2): kind=restart is NEVER adopted/swept by
                # promote.sh or the launcher (D-FA4.3) — restart.sh owns it.
                uj.journal_update_field(
                    install_dir,
                    "in_flight",
                    {
                        "kind": "restart",
                        "target": cur_ver,
                        "started_at": now_iso(),
                        "flipped": False,
                        "owner_pid": os.getpid(),
                        "run_id": run_id,
                    },
                )
                uj.write_pending_op(install_dir, op)
            except (JournalTorn, OSError, KeyError) as exc:
                # NIT-2 (P2.2 tidy cycle-3 carry-over, closed P2.3 B3.5):
                # unwind BEFORE releasing the lock. Releasing first opened
                # a window where a concurrent action acquires the lock and
                # reads a half-unwound journal (in_flight set, owner gone)
                # → pipeline-busy mis-report / adoption on a dead arm.
                # Clearing under our own lock keeps the journal coherent
                # for the next owner.
                # Un-wedge: in_flight is written BEFORE write_pending_op —
                # an arming failure between the two would leave the journal
                # claiming a live txn with no pending_op. Best-effort clear
                # (the block never raises; a failed unwind is logged, not
                # raised).
                try:
                    uj.journal_update_field(install_dir, "in_flight", None)
                except Exception as unwind_exc:
                    logger.warning(
                        "system_restart: in_flight unwind after arming "
                        "failure FAILED — journal may be wedged (manual "
                        "repair: journal_update in_flight null): %s",
                        unwind_exc,
                    )
                journal_lock_release(install_dir)
                return (
                    f"Error: system_restart failed while arming: {type(exc).__name__}: {exc} "
                    "(partial arm unwound best-effort; no restart scheduled — "
                    "inspect release_info(section=journal) if this repeats)"
                )

            marker_ok = _set_execution_marker(
                manager,
                current_instance_id,
                {
                    "kind": "restart",
                    "env": self_env,
                    "run_id": run_id,
                    "target": cur_ver,
                    "reason": reason,
                    "install_dir": str(install_dir),
                    "scripts_dir": str(scripts_dir),
                    "port": _self_port(manager),
                },
            )
            return "\n".join(
                [
                    f"RESTART SCHEDULED — run_id={run_id} env={self_env} mode=graceful-now reason=\"{reason}\"",
                    "executes: after this turn (deferred post-turn trigger; the "
                    "fallback is restart.sh's bounded waiter / boot sweep)",
                    "expected downtime 15-90s (SINGLE-TERM + launcher re-exec + boot preflight)",
                    f"post-restart: ask me to run upgrade_status(run_id=\"{run_id}\") or release_info(section=current)",
                    f"journal: releases/state.json pending-op opened (kind=restart, started_at={op.armed_at}, owner=exec-pending)",
                    f"trigger: {'post-turn-callback armed' if marker_ok else 'post-turn callback unavailable — fallback (bounded waiter/boot sweep)'}",
                    busy,
                ]
            )
        except Exception as exc:  # never raise — structured error string
            logger.warning(
                "system_restart failed for instance=%s: %s", current_instance_id, exc
            )
            return f"Error: system_restart failed: {type(exc).__name__}: {exc}"

    system_restart._full_doc_ = """\
Schedule an intentional restart of THIS daemon's own environment
(P2.2). Arm → return → poll: the tool returns "RESTART SCHEDULED
run_id=..." BEFORE any stop signal; the daemonized restart.sh fires at
exact turn-end (post-turn callback; bounded-waiter/boot-sweep fallback)
and performs a SINGLE-TERM stop (stop-ensemble.sh contract — NEVER a raw
kill) followed by a detached launcher re-exec and a /livez ≤60s gate.

Args:
    target_env: MUST equal the daemon's own environment
        (dev|demo|live|sandbox; the staged ENSEMBLE_SELF_ENV marker).
        Marker absent → refused env-marker-absent (fail-closed; the read
        tools still answer). Cross-env → refused env-self-match.
    reason: Free-text, journaled (audit trail).
    user_confirmed: Accepted for schema stability; ignored on non-live
        (no human-confirmation gate per the user directive).
    mode: Must be "graceful-now" (anything else → unknown-mode).
    nonce: Accepted for schema stability (future live opt-in); ignored.
    dry_run: DEFAULT TRUE (D-FA2.2) — a hallucinated parameter set must
        never execute a real restart. dry_run=true returns a preview +
        plan with ZERO mutation; dry_run=false arms the restart.

LIVE: refused outright this initiative (reason=live-restart-refused —
A2; the refusal points at the manual procedure; no gate, no override,
no dry-run exception). demo/dev/sandbox: free (env-derivation +
self-match guard only) but journaled + lock-protected.

Refusal reasons (distinct tokens): unknown-mode, invalid-target-env,
env-marker-absent, env-self-match, live-restart-refused, pipeline-busy
(open txn / armed pending-op / lock held — names the active run_id),
restart-under-burst-abort (launcher exit-1 latch), executor-scripts-
unavailable, no-staged-install, journal-unavailable (install dir
resolves but the journal is torn/absent).

Never auto-retry a refusal — relay it verbatim to the user (the LLM
never decides go/rollback).
"""

    @register_tool_category("system_upgrade")
    @tool
    async def system_upgrade(
        target_env: str,
        version: str | None = None,
        user_confirmed: bool = False,
        dry_run: bool = True,
        nonce: str | None = None,
    ) -> str:
        """Run the P2.1 promote pipeline for THIS daemon's environment (armed → poll via upgrade_status). Use tool_help("system_upgrade") for details."""
        try:
            label = "UPGRADE"
            self_env, install_dir, refusal = _actor_env_gate(label, target_env)
            if refusal:
                return refusal

            # Journal + pipeline state (shared by dry-run and armed paths).
            # Torn/absent journal ≠ no-install-dir (review nit #11 — same
            # split as system_restart): distinct refusal token so the
            # diagnosis matches the condition.
            journal: dict[str, Any] | None = None
            journal_status = "unreadable"
            if install_dir is not None:
                try:
                    uj.reconcile_pending_op(install_dir)
                except Exception as exc:  # best-effort sweep — never gates
                    logger.warning(
                        "system_upgrade: reconcile_pending_op failed for "
                        "%s: %s — continuing on journal read status",
                        install_dir, exc,
                    )
                journal, journal_status = _journal_read(install_dir)
            if install_dir is None:
                return _refusal(
                    label,
                    "no-staged-install",
                    "no staged install dir resolves for this env — upgrades act "
                    "on a staged releases/ install (run stage.sh first).",
                )
            if journal is None:
                return _refusal(
                    label,
                    "journal-unavailable",
                    f"journal at {install_dir / 'releases' / 'state.json'} is "
                    f"{journal_status} (torn/absent) — halt-for-human: repair "
                    "the journal before any pipeline action (reads still answer).",
                )

            # Target resolution: explicit version or latest staged.
            releases = [r for r in _scan_releases(install_dir, journal) if r.is_release]
            current_target, cur_note = _current_symlink(install_dir)
            cur_ver = current_target.rsplit("/", 1)[-1] if current_target else None
            quarantined_list = (
                [str(v) for v in journal.get("quarantined")]
                if isinstance(journal.get("quarantined"), list)
                else []
            )
            if version is None:
                if cur_note:
                    return _refusal(
                        label, "layout-divergence", f"current symlink issue: {cur_note}"
                    )
                staged_versions = [
                    r.name
                    for r in releases
                    if r.name != cur_ver and r.name not in quarantined_list
                ]
                if not staged_versions:
                    return _refusal(
                        label,
                        "target-not-staged",
                        "no version given and no staged-but-not-current release "
                        "found. Run release_info(section=releases) and pass an "
                        "explicit version.",
                    )
                # N6 (P2.3 B3.5): semver-aware pick — lexical sorted()[-1]
                # made 1.2.9 beat 1.2.10. Explicit version= selection is
                # unaffected (this branch only picks the default target).
                version = max(staged_versions, key=_version_sort_key)
            if version in quarantined_list:
                return _refusal(
                    label,
                    "target-quarantined",
                    f"version '{version}' is QUARANTINED (prior gate failure) — "
                    "quarantine clears only by re-staging the version.",
                )
            tstate, manifest = _target_release_state(install_dir, version)
            if tstate == "target-not-staged":
                return _refusal(
                    label,
                    "target-not-staged",
                    f"releases/{version} not found. Run release_info(section=releases).",
                )
            if tstate == "manifest-unsafe":
                rb = manifest.get("rollback_safe") if manifest else None
                return _refusal(
                    label,
                    "manifest-unsafe",
                    f"target manifest rollback_safe={str(rb).lower()} (drop-release) — "
                    "halt-for-human.",
                )

            # Entry-side anti-flapping state (D-FA4.2: ENTRY only).
            cap = _rollback_count_24h(journal)
            if cap is not None and cap >= ROLLBACK_CAP_24H:
                return _refusal(
                    label,
                    "rollback-cap-exceeded",
                    f"({ROLLBACK_CAP_24H}/24h) — halted-for-human; see "
                    "release_info(section=journal). ADR-005 D2.",
                )
            cooldown = _cooldown_state(journal)
            held_run = journal_lock_run_id(install_dir)
            in_flight = _in_flight_state(journal)
            pending = uj.read_pending_op(install_dir)
            lock_line = (
                "lock: free"
                if held_run is None
                else f"lock: HELD (run_id={held_run})"
            )
            txn_line = (
                "in-flight=none"
                if in_flight is None
                else f"in-flight={_in_flight_summary(journal)}"
            )
            cap_line = _rollback_window_summary(journal)
            busy = await _busy_advisory(manager, current_instance_id)

            # ── dry_run: preflight (+ live nonce issue, §3.1 row 3) ──────
            if dry_run:
                bin_ver = manifest.get("binary_version", "?")
                quar = (
                    [str(v) for v in (journal.get("quarantined") or [])]
                    if isinstance(journal.get("quarantined"), list)
                    else []
                )
                lines = [
                    f"UPGRADE PREFLIGHT (dry-run) — env={self_env} target={version}",
                    f"current={cur_ver or 'none'} (via releases/current)"
                    + (" rollback_safe=true" if cur_ver else ""),
                    f"target staged: releases/{version} manifest "
                    f"rollback_safe={str(manifest.get('rollback_safe')).lower()} "
                    f"known_schema_gen={manifest.get('known_schema_gen', '?')} "
                    f"binary_version={bin_ver}",
                    f"journal: current={journal.get('current')} previous={journal.get('previous')} "
                    f"{cap_line} quarantine={quar}",
                    f"{txn_line}",
                    f"{lock_line}",
                    f"cooldown={cooldown}",
                    busy,
                    "PLAN: pg_dump preflight → stop (SINGLE-TERM) → flip "
                    f"current→{version} → start → gate (/livez ≤60s, /readyz "
                    "≤120s, 300s soak) → commit | auto-rollback",
                ]
                if self_env == "live":
                    run_id = mint_run_id()
                    action = PendingAction(
                        run_id=run_id,
                        nonce=mint_nonce(),
                        kind="upgrade",
                        env=self_env,
                        target=version,
                        issued_to_instance=current_instance_id,
                    )
                    uj.store_pending_action(install_dir, action)
                    lines.append(
                        "CONFIRMATION REQUIRED (live): nonce "
                        f"{nonce_grouped(action.nonce)} — the user must reply "
                        "with this nonce; then call system_upgrade("
                        f"user_confirmed=true, nonce=\"{nonce_grouped(action.nonce)}\"). "
                        "Nonce single-use, expires in 15min."
                    )
                    lines.append(
                        "NOTE: this preflight persisted ONLY the nonce "
                        f"pending-action (run_id={run_id}) — no pipeline mutation."
                    )
                else:
                    lines.append(
                        "NO mutation happened (dry-run). Call again with "
                        "dry_run=false to arm the promote."
                    )
                return "\n".join(lines)

            # ── armed ──────────────────────────────
            # LIVE: the 3-factor gate (§4.3) BEFORE any live action.
            confirmed_source: str | None = None
            confirmed_action: PendingAction | None = None
            confirmed_msg_id: str | None = None
            if self_env == "live":
                factor_failures: list[str] = []
                if not user_confirmed:
                    factor_failures.append(
                        "user-confirmation-missing: the user_confirmed param is "
                        "false — relay to the user: reply with the nonce to authorize"
                    )
                # Factor 2: server-side user-origin marker on THIS turn.
                window = None
                windows = getattr(manager, "_user_origin_windows", None)
                if isinstance(windows, dict):
                    window = windows.get(current_instance_id)
                if window is None:
                    factor_failures.append(
                        "user-confirmation-missing: this turn was not triggered "
                        "by a whitelisted user-origin message "
                        f"(USER_ORIGIN_SOURCES={sorted(USER_ORIGIN_SOURCES)})"
                    )
                else:
                    expires = parse_iso_utc(window.get("expires_at"))
                    # Fail-closed (review nit #5): an unparseable/absent
                    # expires_at counts as EXPIRED — same refusal flavor —
                    # never as still-valid (fail-open would let a corrupt
                    # window marker unlock the live gate).
                    if expires is None or datetime.now(tz=timezone.utc) > expires:
                        factor_failures.append(
                            "user-confirmation-missing: the user-origin window "
                            f"expired at {window.get('expires_at')} — ask again in "
                            "a fresh user turn"
                        )
                    else:
                        confirmed_source = window.get("source")
                        confirmed_msg_id = window.get("message_id")
                # Factor 1+3 combined when the param IS set.
                if user_confirmed and window is not None:
                    if not nonce:
                        factor_failures.append(
                            "user-confirmation-missing: no nonce supplied — a "
                            "fabricated user_confirmed alone never unlocks live "
                            "(relay the dry-run nonce to the user)"
                        )
                    else:
                        action = uj.find_pending_action_by_nonce(install_dir, nonce)
                        if action is None:
                            factor_failures.append(
                                "nonce-mismatch: nonce does not match any pending "
                                "nonce for this install — re-run dry_run"
                            )
                        elif action.consumed_at is not None:
                            factor_failures.append(
                                "nonce-already-used: nonce consumed at "
                                f"{action.consumed_at} — re-run dry_run"
                            )
                        elif action.issued_to_instance != current_instance_id:
                            # MAJOR-1(b) (P2.2 fix pass 2026-08-23): the
                            # nonce is instance-bound (issued_to_instance is
                            # recorded at mint, upgrade_journal.py). A nonce
                            # minted for a DIFFERENT instance must not arm
                            # from this one — fail-closed, matching the
                            # window/TTL checks (also closes reviewer N2:
                            # the field was recorded but never checked).
                            factor_failures.append(
                                "nonce-instance-mismatch: the nonce was "
                                "issued to another instance (or an "
                                "unattributed record) — re-run dry_run from "
                                "THIS instance to mint a fresh nonce"
                            )
                        else:
                            ttl = parse_iso_utc(action.ttl_expires_at)
                            # M1 (P2.2 fix pass): fail CLOSED on an
                            # unparseable/absent ttl_expires_at — an
                            # unparseable TTL counts as EXPIRED (same
                            # refusal flavor), never as still-valid
                            # (fail-open would let a corrupt nonce record
                            # outlive its TTL; mirrors the adjacent window
                            # expires_at check above).
                            if ttl is None or datetime.now(tz=timezone.utc) > ttl:
                                factor_failures.append(
                                    f"nonce-expired: nonce issued at "
                                    f"{action.issued_at}, TTL 15min elapsed. "
                                    "Re-run dry_run to obtain a fresh nonce."
                                )
                            elif (
                                action.target != version
                                or action.kind != "upgrade"
                                or action.env != self_env
                            ):
                                # MAJOR-2 (P2.2 fix pass 2026-08-23): the
                                # nonce is ACTION-BOUND (§4.2(b)/§4.3) — it
                                # authorizes exactly the (kind, env, target)
                                # triple it was minted for. An armed call
                                # naming a DIFFERENT version than the
                                # dry_run that minted the nonce must refuse
                                # even with all 3 factors otherwise green.
                                factor_failures.append(
                                    "nonce-action-mismatch: the nonce was "
                                    f"minted for kind={action.kind} "
                                    f"env={action.env} target="
                                    f"{action.target or '?'} but this call "
                                    f"arms kind=upgrade env={self_env} "
                                    f"target={version} — the nonce is "
                                    "action-bound (§4.2(b)); re-run dry_run "
                                    "for THIS action"
                                )
                            else:
                                # Factor 3: the triggering HUMAN message CONTENT
                                # contains the nonce — read the MessageQueue row
                                # by message_id ONLY (S-07: single row, no bulk
                                # history). Row wiped/unreadable → fail-closed
                                # (D-FA3.3 nonce-verification-unavailable).
                                row_content: str | None = None
                                try:
                                    repo = getattr(manager, "_queue_repository", None)
                                    row = (
                                        await asyncio.to_thread(repo.get, confirmed_msg_id)
                                        if repo is not None and confirmed_msg_id
                                        else None
                                    )
                                    row_content = getattr(row, "content", None)
                                except Exception:
                                    row_content = None
                                if row_content is None:
                                    factor_failures.append(
                                        "nonce-verification-unavailable: the "
                                        "triggering message row could not be read "
                                        "(daemon restarted since issuance? the row "
                                        "is wiped at boot) — re-run dry_run (R-SR19)"
                                    )
                                elif not uj.nonce_in_content(action.nonce, row_content):
                                    factor_failures.append(
                                        "user-confirmation-missing: this turn was "
                                        "not triggered by a user message carrying "
                                        f"nonce {nonce_grouped(action.nonce)}. Relay "
                                        "to the user: reply with the nonce to authorize."
                                    )
                                else:
                                    confirmed_action = action
                if factor_failures:
                    return _live_gate_summary(label, factor_failures)

            # Armed preconditions (dynamic state — refusals, not notes).
            if in_flight is not None:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"in-flight txn kind={in_flight.get('kind', '?')} "
                    f"target={in_flight.get('target', '?')} (run upgrade_status "
                    "to follow it)",
                )
            if pending is not None:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"pending op run_id={pending.run_id} kind={pending.kind} "
                    f"armed_at={pending.armed_at} — poll upgrade_status",
                )
            # NIT-1 (P2.2 tidy cycle-3 carry-over, closed P2.3 B3.5): the
            # pre-tidy conjunct (cooldown != "clear" and not
            # cooldown.startswith("clear")) survived the simplification
            # as a loose PREFIX match. Pin the test to the exact value
            # domain of _cooldown_state's clear sentinel — "clear" is the
            # only clear-valued state the helper emits, and a prefix test
            # would silently admit any future "clear…"-prefixed state.
            if cooldown != "clear":
                return _refusal(
                    label,
                    "cooldown-active",
                    f"rollback cooldown {cooldown} (ADR-005: 10-min "
                    "anti-flapping) — promotes refused until it lapses",
                )
            if held_run is not None:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"per-env lock held (run_id={held_run}) — retry via upgrade_status",
                )
            scripts_dir = _resolve_scripts_dir(install_dir)
            if scripts_dir is None or not (scripts_dir / "promote.sh").is_file():
                return _refusal(
                    label,
                    "executor-scripts-unavailable",
                    "cannot resolve scripts/upgrade/promote.sh (set "
                    "ENSEMBLE_UPGRADE_SCRIPTS_DIR or run from a source checkout)",
                )

            # ARM (§6.3): lock + pending-op written BEFORE returning.
            # Live: carry the nonce record's run_id — the dry-run that minted
            # the nonce and the armed op share one cross-death join key. The
            # nonce was fully VALIDATED read-only by the 3-factor gate above
            # (match / unused / unexpired); the single-use burn happens only
            # AFTER the lock is ours, so a busy-lock race refusal never
            # wastes the nonce (no re-run of dry_run for a lock never taken).
            run_id = (
                confirmed_action.run_id
                if confirmed_action is not None
                else mint_run_id()
            )
            acquired, busy_run = journal_lock_acquire(install_dir, run_id)
            if not acquired:
                return _refusal(
                    label,
                    "pipeline-busy",
                    f"per-env lock held (run_id={busy_run or '?'}) — retry via upgrade_status",
                )
            try:
                if confirmed_action is not None:
                    uj.consume_pending_action(
                        install_dir, confirmed_action, confirmed_msg_id
                    )
                uj.journal_init(install_dir)
                uj.ensure_extensions(install_dir)
                op = PendingOp(
                    run_id=run_id,
                    kind="promote",
                    env=self_env,
                    target=version,
                    reason=f"upgrade to {version}",
                    armed_by_instance=current_instance_id,
                    owner_pid=os.getpid(),
                    owner_kind="tool-arm",
                    expires_at=iso_plus(now_iso(), uj.PENDING_OP_EXPIRE_PROMOTE_S),
                    nonce=confirmed_action.nonce if confirmed_action else None,
                    nonce_consumed=confirmed_action is not None,
                    confirmed_by_human=confirmed_source is not None,
                    confirmed_source=confirmed_source,
                )
                uj.write_pending_op(install_dir, op)
            except (JournalTorn, OSError, KeyError) as exc:
                journal_lock_release(install_dir)
                return f"Error: system_upgrade failed while arming: {type(exc).__name__}: {exc}"

            marker_ok = _set_execution_marker(
                manager,
                current_instance_id,
                {
                    "kind": "promote",
                    "env": self_env,
                    "run_id": run_id,
                    "target": version,
                    "install_dir": str(install_dir),
                    "scripts_dir": str(scripts_dir),
                    "port": _self_port(manager),
                },
            )
            return "\n".join(
                [
                    f"UPGRADE ARMED — run_id={run_id} env={self_env} target={version} mode=promote",
                    "executes: after this turn completes (deferred — daemonized promote.sh)",
                    f"watch: upgrade_status(run_id=\"{run_id}\") for phase transitions; terminal state readable post-restart",
                    f"journal: releases/state.json txn opened (started_at={op.armed_at}, owner=exec-pending)",
                    (
                        f"live-confirmation: nonce consumed (confirmed_source={confirmed_source})"
                        if confirmed_action is not None
                        else "confirmation: none required (non-live target)"
                    ),
                    f"trigger: {'post-turn-callback armed' if marker_ok else 'post-turn callback unavailable — fallback (boot sweep)'}",
                    busy,
                ]
            )
        except Exception as exc:  # never raise — structured error string
            logger.warning(
                "system_upgrade failed for instance=%s: %s", current_instance_id, exc
            )
            return f"Error: system_upgrade failed: {type(exc).__name__}: {exc}"

    system_upgrade._full_doc_ = """\
Arm the P2.1 promote pipeline (stop → flip → start → gate → commit |
auto-rollback) for THIS daemon's own environment. Arm → return → poll:
the tool returns "UPGRADE ARMED run_id=..." immediately (armed/deferred —
no execution inside the call; N5 wording, P2.3 B3.5) — the daemonized
promote.sh fires at end-of-turn and survives the daemon's own death (the
normal case when upgrading the daemon you run on).
Track it with upgrade_status(run_id=...); the terminal state
(committed / rolled-back / halted-for-human) is readable post-restart.

Args:
    target_env: MUST equal the daemon's own environment
        (dev|demo|live|sandbox; staged ENSEMBLE_SELF_ENV marker).
        Marker absent → env-marker-absent; cross-env → env-self-match.
    version: Target release name; default: latest staged-but-not-current
        release (quarantined ones skipped; "latest" is semver-aware —
        1.2.10 outranks 1.2.9, non-numeric tags sort lexically below
        numeric ones).
    user_confirmed: LIVE only (ignored on demo/dev/sandbox — the user
        directive makes non-live free). Necessary, NEVER sufficient: a
        fabricated true does not unlock live.
    dry_run: DEFAULT TRUE (D-FA2.2). dry_run=true → preflight (plan +
        journal/lock/cooldown state) with NO pipeline mutation; on live
        it issues the confirmation nonce. dry_run=false → arm.
    nonce: LIVE only — the nonce issued by a prior dry_run, which the
        user must have echoed back in their reply.

LIVE 3-factor gate (§4.3, enforced server-side BEFORE any live action):
(1) user_confirmed=true param; (2) this turn triggered by a
whitelisted user-origin message (USER_ORIGIN_SOURCES: api + the
telegram/webhook/whatsapp/discord/slack channel prefixes); (3) that
HUMAN message's CONTENT contains the action-binding nonce
(single-use, TTL 15min, persisted in the journal — survives daemon
death). A fabricated param fails (2); a self-echoed nonce in an
agent/internal-origin message fails (2)+(3). NOTE: this initiative
never exercises the live happy path — live refusals only.

Refusal reasons (distinct tokens): invalid-target-env,
env-marker-absent, env-self-match, target-not-staged,
target-quarantined, manifest-unsafe (rollback_safe=false),
rollback-cap-exceeded (3/24h), cooldown-active, pipeline-busy (open
txn / armed pending-op / lock held — names the run_id),
user-confirmation-missing, nonce-mismatch, nonce-expired (also when
ttl_expires_at is unparseable — fail-closed), nonce-instance-mismatch
(the nonce was minted for another instance), nonce-action-mismatch
(the nonce was minted for a different kind/env/target than this call —
the nonce is action-bound, §4.2(b)), nonce-already-used,
nonce-verification-unavailable, executor-scripts-unavailable,
no-staged-install, journal-unavailable (install dir resolves but the
journal is torn/absent), layout-divergence.

Never auto-retry a refusal — relay it verbatim (the LLM never decides
go/rollback).
"""

    return [release_info, upgrade_status, system_restart, system_upgrade]
