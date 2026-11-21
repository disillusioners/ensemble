"""Deterministic daily digest over the tool-usage ledger (Phase 3).

Reads ``tool_usage_daily`` (Phase 1, frozen) and renders a deterministic
markdown digest. No writes to the ledger, no clock/random/live-registry
reads in the rollup body (the generated-at header is the only wall-clock
surface and is isolated so two runs on the same fixture + date are
byte-identical modulo that header).

Windows (D-7, frozen):
    D = UTC date of the fire (run date).
    "last-24h"        := complete previous UTC day (D-1).
    "cumulative-week" := [D-7, D-1] complete UTC days.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from daemon.repositories.tool_usage.models import ToolUsageDaily

logger = logging.getLogger(__name__)

#: Weight fallback for tools absent from the committed weight artifact.
FALLBACK_WEIGHT = 230

#: Near-zero threshold (calls in the 7d window) — mirrors the FE's
#: ``NEAR_ZERO_CALLS_7D``.
NEAR_ZERO_CALLS_7D = 3

#: Env var naming the canonical digest root (D-6).
DIGEST_ROOT_ENV = "ENSEMBLE_TOOL_USAGE_DIGEST_ROOT"


def _code_root() -> Path:
    # Frozen-aware: under PyInstaller, __file__ resolves inside the
    # ephemeral _MEIPASS archive (no .agents/ subdir, wiped at exit);
    # use sys.executable's parent (the install dir) instead, mirroring
    # daemon/graph.py frozen-aware agents base + daemon/manager.py.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # daemon/services/tool_usage_digest.py -> parents[2] == repo/install root
    return Path(__file__).resolve().parents[2]


def digest_root() -> Path:
    """Resolve the canonical digest root (D-6).

    Env var wins; otherwise the daemon's own install root (deterministic,
    no manager.workdir phantom, no live project read).
    """
    env = os.environ.get(DIGEST_ROOT_ENV)
    if env and env.strip():
        return Path(env.strip())
    return _code_root() / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"


def _artifact_dir() -> Path:
    return _code_root() / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1"


def _load_json_artifact(name: str) -> Any:
    path = _artifact_dir() / name
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("tool_usage_digest: could not load artifact %s: %s", name, exc)
        return {}


def tool_weights() -> dict[str, int]:
    raw = _load_json_artifact("tool-weights.json")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        if isinstance(v, (int, float)):
            out[str(k)] = int(v)
    return out


def allowlists() -> dict[str, list[str]]:
    raw = _load_json_artifact("allowlists.json")
    if not isinstance(raw, dict):
        return {}
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        return {}
    out: dict[str, list[str]] = {}
    for agent, tools in agents.items():
        if isinstance(tools, list):
            out[str(agent)] = [str(t) for t in tools]
    return out


def _weight_for(tool_name: str) -> int:
    w = tool_weights().get(tool_name)
    if isinstance(w, int) and w > 0:
        return w
    return FALLBACK_WEIGHT


def _parse_run_date(run_date: str | None) -> date:
    if run_date:
        try:
            return date.fromisoformat(run_date)
        except ValueError as exc:
            logger.debug("tool_usage_digest: invalid run_date %r: %s", run_date, exc)
    return datetime.now(timezone.utc).date()


def _window_days(run_date: date) -> tuple[date, list[date]]:
    """Return (last24h_day, week_days)."""
    last24 = run_date - timedelta(days=1)
    week = [run_date - timedelta(days=i) for i in range(7, 0, -1)]  # D-7..D-1
    return last24, week


def _day_str(d: date) -> str:
    return d.isoformat()


def rollup(engine: Engine, run_date: str | None = None) -> dict[str, Any]:
    """Build the structured rollup for a run date.

    Returns a dict with keys: run_date, last24_day, week_days, projects.
    projects: list of {project_id, tools: [{tool_name, weight, calls_24h,
    calls_7d, agents: [{agent_id, calls_24h, calls_7d, allow_listed}]}]}.
    zero_near_zero: list of {tool_name, calls_7d, allowed_agents} for allow-listed tools with <= NEAR_ZERO_CALLS_7D calls in 7d.
    tool_allowed_agents: dict mapping tool_name -> sorted list of agents that allow it.
    """
    rd: date = _parse_run_date(run_date)
    last24_day, week_days = _window_days(rd)
    week_strs: set[str] = {_day_str(d) for d in week_days}
    last24_str: str = _day_str(last24_day)

    # Collect raw rows for the week window + last24.
    # last24 is already in week (D-1 is the last of D-7..D-1), so week_strs covers it.

    # Query: rows where day in week_strs.
    with Session(engine) as session:
        stmt = select(ToolUsageDaily).where(ToolUsageDaily.day.in_(sorted(week_strs)))
        rows: list[ToolUsageDaily] = list(session.exec(stmt))

    # Aggregate.
    # project -> tool -> {calls_24h, calls_7d, agents: agent -> {24h,7d}}
    projects: dict[str, dict[str, Any]] = {}
    for r in rows:
        proj = r.project_id
        tool = r.tool_name
        agent = r.agent_id
        day = r.day
        cnt = int(r.call_count or 0)
        p = projects.setdefault(proj, {"project_id": proj, "tools": {}})
        t = p["tools"].setdefault(tool, {"tool_name": tool, "calls_24h": 0, "calls_7d": 0, "agents": {}})
        a = t["agents"].setdefault(agent, {"agent_id": agent, "calls_24h": 0, "calls_7d": 0})
        t["calls_7d"] += cnt
        a["calls_7d"] += cnt
        if day == last24_str:
            t["calls_24h"] += cnt
            a["calls_24h"] += cnt

    # Build structured output.
    wl: dict[str, int] = tool_weights()
    al: dict[str, list[str]] = allowlists()
    # Invert allowlists: tool -> set of agents that allow it.
    tool_allowed_agents: dict[str, set[str]] = {}
    for agent, tools in al.items():
        for t in tools:
            tool_allowed_agents.setdefault(t, set()).add(agent)

    project_list: list[dict[str, Any]] = []
    for proj in sorted(projects.keys()):
        p = projects[proj]
        tool_list: list[dict[str, Any]] = []
        for tool in sorted(p["tools"].keys()):
            t = p["tools"][tool]
            weight = _weight_for(tool)
            score = weight * t["calls_7d"]
            agents_out: list[dict[str, Any]] = []
            for agent in sorted(t["agents"].keys()):
                a = t["agents"][agent]
                agents_out.append({
                    "agent_id": agent,
                    "calls_24h": a["calls_24h"],
                    "calls_7d": a["calls_7d"],
                })
            # allow_listed per agent: tool in that agent's allow list
            for ao in agents_out:
                ao["allow_listed"] = tool in al.get(ao["agent_id"], [])
            tool_list.append({
                "tool_name": tool,
                "weight": weight,
                "calls_24h": t["calls_24h"],
                "calls_7d": t["calls_7d"],
                "score": score,
                "allow_listed_any": tool in tool_allowed_agents,
                "allowed_agents": sorted(tool_allowed_agents.get(tool, set())),
                "agents": agents_out,
            })
        # Sort: score DESC, tool_name ASC, project_id ASC (project already grouped).
        tool_list.sort(key=lambda x: (-x["score"], x["tool_name"]))
        project_list.append({"project_id": proj, "tools": tool_list})

    # Zero/near-zero: allow-listed tools with 0 calls in window.
    # Collect set of all allow-listed tools.
    all_allowed_tools: set[str] = set()
    for agent, tools in al.items():
        for t in tools:
            all_allowed_tools.add(t)

    zero_near_zero: list[dict[str, Any]] = []
    for tool in sorted(all_allowed_tools):
        # Find if it appears in any project's tool list with calls_7d==0 or <=NEAR_ZERO
        # A tool may not appear at all (0 calls) -> zero.
        present_calls: dict[str, int] = {}
        for p in project_list:
            for t in p["tools"]:
                if t["tool_name"] == tool:
                    present_calls[p["project_id"]] = t["calls_7d"]
        # If not present in a project, calls_7d=0 for that project.
        # We'll list at global level: if total calls_7d across projects == 0 -> zero.
        total_7d = sum(present_calls.values()) if present_calls else 0
        if total_7d <= NEAR_ZERO_CALLS_7D:
            zero_near_zero.append({"tool_name": tool, "calls_7d": total_7d, "allowed_agents": sorted(tool_allowed_agents.get(tool, set()))})

    return {
        "run_date": _day_str(rd),
        "last24_day": last24_str,
        "week_days": [_day_str(d) for d in week_days],
        "projects": project_list,
        "zero_near_zero": zero_near_zero,
        "tool_allowed_agents": {k: sorted(v) for k, v in tool_allowed_agents.items()},
    }


def render_markdown(rollup_data: dict[str, Any], generated_at: str | None = None) -> str:
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    lines.append(f"# Tool Usage Digest — {rollup_data['run_date']}")
    lines.append("")
    lines.append(f"Generated at: {generated_at}")
    lines.append("")
    lines.append(f"Window (last-24h): {rollup_data['last24_day']} (complete UTC day)")
    lines.append(f"Window (cumulative-week): {rollup_data['week_days'][0]} .. {rollup_data['week_days'][-1]} (complete UTC days)")
    lines.append("")

    for p in rollup_data["projects"]:
        lines.append(f"## Project: {p['project_id']}")
        lines.append("")
        lines.append("### Tools (sorted by schema weight × calls, descending)")
        lines.append("")
        lines.append("| Tool | Weight | Calls 24h | Calls 7d | Score | Allow-listed |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for t in p["tools"]:
            al = "yes" if t["allow_listed_any"] else "no"
            lines.append(f"| {t['tool_name']} | {t['weight']} | {t['calls_24h']} | {t['calls_7d']} | {t['score']} | {al} |")
        lines.append("")
        # Per-agent breakdown
        lines.append("### Per-agent breakdown")
        lines.append("")
        for t in p["tools"]:
            if not t["agents"]:
                continue
            lines.append(f"#### {t['tool_name']}")
            for a in t["agents"]:
                marker = " (allow-listed)" if a["allow_listed"] else ""
                lines.append(f"- {a['agent_id']}: 24h={a['calls_24h']} 7d={a['calls_7d']}{marker}")
            lines.append("")
        lines.append("")

    # Zero / near-zero section
    lines.append("## Zero / near-zero candidates (allow-listed, ≤3 calls in 7d)")
    lines.append("")
    for z in rollup_data["zero_near_zero"]:
        agents = ", ".join(z["allowed_agents"]) if z["allowed_agents"] else "(none)"
        lines.append(f"- {z['tool_name']}: calls_7d={z['calls_7d']} — allowed by: {agents}")
    lines.append("")

    return "\n".join(lines)


def digest_path(run_date: str | None = None) -> Path:
    rd: date = _parse_run_date(run_date)
    root = digest_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{rd.isoformat()}.md"


def write_digest(engine: Engine, run_date: str | None = None) -> tuple[str, Path]:
    """Render + write the digest. Returns (markdown, path)."""
    data = rollup(engine, run_date)
    md = render_markdown(data)
    path = digest_path(run_date)
    path.write_text(md, encoding="utf-8")
    return md, path


__all__ = [
    "FALLBACK_WEIGHT",
    "NEAR_ZERO_CALLS_7D",
    "DIGEST_ROOT_ENV",
    "digest_root",
    "rollup",
    "render_markdown",
    "digest_path",
    "write_digest",
    "tool_weights",
    "allowlists",
]
