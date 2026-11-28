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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from daemon.repositories.tool_usage.models import ToolUsageDaily

logger = logging.getLogger(__name__)

#: Weight fallback for tools absent from the committed weight artifact.
FALLBACK_WEIGHT = 230

#: §6.1 — the two verbatim heading variants.  The renderer NEVER hard-codes
#: the choice: `rollup()` computes it from the rendered rows' weights.
WEIGHT_HEADING = "### Tools (sorted by schema weight × calls 7d, descending)"
FALLBACK_WEIGHT_HEADING = (
    "### Tools (sorted by calls 7d, descending — no per-tool schema weights are available; "
    "every tool uses the 230-token average fallback, so the weight does not discriminate)"
)

#: Near-zero threshold (calls in the 7d window) — mirrors the FE's
#: ``NEAR_ZERO_CALLS_7D``.
NEAR_ZERO_CALLS_7D = 3

#: Env var naming the canonical digest root (D-6).
DIGEST_ROOT_ENV = "ENSEMBLE_TOOL_USAGE_DIGEST_ROOT"

#: Test seam for the artifact dir (documented test-only).
ARTIFACT_DIR_ENV = "ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR"

#: Relative digest dir under the install root.
DIGEST_RELATIVE_DIR = Path(".agents") / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"

#: Payload dir name (R-PAYLOAD).
ARTIFACT_DIRNAME = "tool-usage-artifacts"


class DigestRootUnresolved(RuntimeError):
    """Raised when the canonical digest destination cannot be VERIFIED."""


def _verified_install_root(candidate: Path | None) -> Path | None:
    if candidate is None:
        return None
    try:
        if (candidate / "releases").is_dir() or (candidate / "daemon").is_dir():
            return candidate
    except OSError:
        return None
    return None


def _install_root() -> Path:
    from daemon.repositories.source.maintenance_seed import resolve_live_install_root

    root = _verified_install_root(resolve_live_install_root())
    if root is None:
        raise DigestRootUnresolved(
            "Digest destination root unresolved: the live install root could not be verified "
            f"(expected a directory owning 'releases/' or 'daemon/'). Set {DIGEST_ROOT_ENV} explicitly."
        )
    return root


def _code_root() -> Path:
    # `_code_root()` = the root of the executing CODE tree — the RELEASE dir
    # when frozen, the checkout root in source mode. It is valid ONLY as the
    # anchor for read-only data shipped with the code; it is NEVER a write
    # destination.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # daemon/services/tool_usage_digest.py -> parents[2] == repo/install root
    return Path(__file__).resolve().parents[2]


def digest_root() -> Path:
    """Resolve the canonical digest root (D-6 / U8). Env override wins."""
    env = os.environ.get(DIGEST_ROOT_ENV)
    if env and env.strip():
        return Path(env.strip())
    return _install_root() / DIGEST_RELATIVE_DIR


def _artifact_dir() -> Path:
    """Resolve the READ-ONLY artifact dir (R-PAYLOAD)."""
    env = os.environ.get(ARTIFACT_DIR_ENV)
    if env and env.strip():
        return Path(env.strip())
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / ARTIFACT_DIRNAME
    return _code_root() / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1"


def _load_json_artifact(name: str) -> Any:
    path = _artifact_dir() / name
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("tool_usage_digest: could not load artifact %s: %s", name, exc)
        return {}


@dataclass
class AllowSource:
    state: str = "ok"
    schema_version: int = 2
    agents: dict = field(default_factory=dict)
    categories: dict = field(default_factory=dict)
    runtime_only_categories: list = field(default_factory=list)
    privileged_categories: list = field(default_factory=list)
    dynamic_prefixes: list = field(default_factory=list)
    innate_skill_categories: dict = field(default_factory=dict)
    known_tool_names: set = field(default_factory=set)
    unresolved: dict = field(default_factory=lambda: {"allow": [], "deny": []})
    provenance: dict = field(default_factory=dict)
    #: The human-readable cause of a `state="rejected"` verdict.  The logger
    #: alone is not enough — the markdown banner must name the real cause.
    rejection_reason: str = ""

    @property
    def category_keys(self) -> set:
        return set(self.categories.keys()) | set(self.runtime_only_categories)

    @property
    def owned_category(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for cat, tools in (self.categories or {}).items():
            if isinstance(tools, (list, tuple)):
                for t in tools:
                    out[str(t)] = str(cat)
        return out

    @property
    def literal_grants(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for agent, info in (self.agents or {}).items():
            if not isinstance(info, dict):
                out[str(agent)] = set()
                continue
            allow = info.get("allow", [])
            if not isinstance(allow, list):
                allow = []
            lits = set()
            for e in allow:
                if str(e) not in self.category_keys:
                    lits.add(str(e))
            out[str(agent)] = lits
        return out

    @property
    def granted_categories(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for agent, info in (self.agents or {}).items():
            if not isinstance(info, dict):
                out[str(agent)] = set()
                continue
            allow = info.get("allow", [])
            if not isinstance(allow, list):
                allow = []
            cats = set()
            for e in allow:
                if str(e) in self.category_keys:
                    cats.add(str(e))
            out[str(agent)] = cats
        return out

    def deny_entry_for(self, agent_id: str, tool: str) -> Any:
        info = (self.agents or {}).get(agent_id)
        if not isinstance(info, dict):
            return None
        deny = info.get("deny", [])
        if not isinstance(deny, list):
            return None
        if tool in deny:
            return tool
        for entry in deny:
            es = str(entry)
            if es in self.category_keys:
                members = self.categories.get(es, [])
                if isinstance(members, (list, tuple)) and tool in members:
                    return es
        # literal deny where entry equals tool already handled; also check str
        for entry in deny:
            if str(entry) == tool:
                return entry
        return None

    def inherits_all(self, agent_id: str) -> bool:
        info = (self.agents or {}).get(agent_id)
        if not isinstance(info, dict):
            return False
        allow = info.get("allow", [])
        # None allow => treat as empty? pseudocode: if not allow
        if allow is None:
            allow = []
        if not isinstance(allow, list):
            return False
        innate = info.get("innate", [])
        if not isinstance(innate, list):
            innate = []
        extra: list[str] = []
        for skill in innate:
            for cat in self.innate_skill_categories.get(skill, []):
                if cat in self.privileged_categories:
                    continue
                if cat not in allow and cat not in extra:
                    extra.append(cat)
        effective = list(allow) + extra if extra else list(allow)
        return len(effective) == 0

    def is_mcp_shaped(self, name: str) -> bool:
        return name.startswith("mcp_") and "_" in name[4:]

    def is_dynamic_prefix_shaped(self, name: str) -> bool:
        return any(name.startswith(p) for p in (self.dynamic_prefixes or []))

    def category_granted(self, agent_id: str, key: str) -> bool:
        if self.inherits_all(agent_id):
            if key in (self.privileged_categories or []):
                return False
            return key in self.category_keys
        return key in self.granted_categories.get(agent_id, set())

    def grant_reason(self, agent_id: str, tool: str) -> str:
        # literal
        if tool in self.literal_grants.get(agent_id, set()):
            return "literal"
        # category
        for cat in self.granted_categories.get(agent_id, set()):
            members = self.categories.get(cat, [])
            if isinstance(members, (list, tuple)) and tool in members:
                return f"category:{cat}"
        # inherit-all
        if self.inherits_all(agent_id):
            owner = self.owned_category.get(tool)
            if owner is not None and owner not in (self.privileged_categories or []):
                return "inherit-all (empty allow; non-privileged default universe)"
        return "no reason available"


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def allowlist_source() -> AllowSource:
    path = _artifact_dir() / "allowlists.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        logger.warning(
            "tool_usage_digest: allow-list artifact absent at %s — allow-list dimension disabled",
            path,
        )
        return AllowSource(state="absent")
    except OSError:
        logger.warning(
            "tool_usage_digest: allow-list artifact unreadable at %s — allow-list dimension disabled",
            path,
        )
        return AllowSource(state="unreadable")
    except (ValueError, json.JSONDecodeError):
        logger.warning(
            "tool_usage_digest: allow-list artifact malformed at %s — allow-list dimension disabled",
            path,
        )
        return AllowSource(state="malformed")
    if not isinstance(raw, dict):
        logger.warning(
            "tool_usage_digest: allow-list artifact malformed at %s (not a JSON object) — allow-list dimension disabled",
            path,
        )
        return AllowSource(state="malformed")

    sv = raw.get("schema_version")
    agents_raw = raw.get("agents")
    categories = raw.get("categories", {})
    runtime_only = raw.get("runtime_only_categories", [])
    privileged = raw.get("privileged_categories", [])
    dynamic_prefixes = raw.get("dynamic_prefixes", [])
    innate_map = raw.get("innate_skill_categories", {})
    known = raw.get("known_tool_names", [])
    unresolved = raw.get("unresolved", {"allow": [], "deny": []})
    provenance = raw.get("provenance", {})

    if sv != 2:
        found = str(sv) if sv is not None else "none"
        logger.error(
            "tool_usage_digest: ARTIFACT SCHEMA REJECTED (found: %s, required: 2) at %s — allow-list dimension disabled",
            found,
            path,
        )
        return AllowSource(
            state="rejected",
            schema_version=sv if isinstance(sv, int) else 0,
            agents=agents_raw if isinstance(agents_raw, dict) else {},
            categories=categories if isinstance(categories, dict) else {},
            runtime_only_categories=list(runtime_only) if isinstance(runtime_only, list) else [],
            privileged_categories=list(privileged) if isinstance(privileged, list) else [],
            dynamic_prefixes=list(dynamic_prefixes) if isinstance(dynamic_prefixes, list) else [],
            innate_skill_categories=innate_map if isinstance(innate_map, dict) else {},
            known_tool_names=set(known) if isinstance(known, list) else set(),
            unresolved=unresolved if isinstance(unresolved, dict) else {"allow": [], "deny": []},
            provenance=provenance if isinstance(provenance, dict) else {},
            rejection_reason="the artifact's schema_version is not 2 (schema-1 payloads are not read)",
        )

    if not isinstance(agents_raw, dict):
        return AllowSource(state="malformed", schema_version=2)

    for v in agents_raw.values():
        if not isinstance(v, dict) or "deny" not in v:
            logger.error(
                "tool_usage_digest: ARTIFACT SCHEMA REJECTED (found: %s, required: 2) at %s — "
                "a per-agent object lacks the mandatory 'deny' key; allow-list dimension disabled",
                sv,
                path,
            )
            return AllowSource(
                state="rejected",
                schema_version=2,
                agents=agents_raw,
                categories=categories if isinstance(categories, dict) else {},
                runtime_only_categories=list(runtime_only) if isinstance(runtime_only, list) else [],
                privileged_categories=list(privileged) if isinstance(privileged, list) else [],
                dynamic_prefixes=list(dynamic_prefixes) if isinstance(dynamic_prefixes, list) else [],
                innate_skill_categories=innate_map if isinstance(innate_map, dict) else {},
                known_tool_names=set(known) if isinstance(known, list) else set(),
                unresolved=unresolved if isinstance(unresolved, dict) else {"allow": [], "deny": []},
                provenance=provenance if isinstance(provenance, dict) else {},
                rejection_reason="a per-agent object lacks the mandatory 'deny' key",
            )

    return AllowSource(
        state="ok",
        schema_version=2,
        agents=agents_raw,
        categories=categories if isinstance(categories, dict) else {},
        runtime_only_categories=list(runtime_only) if isinstance(runtime_only, list) else [],
        privileged_categories=list(privileged) if isinstance(privileged, list) else [],
        dynamic_prefixes=list(dynamic_prefixes) if isinstance(dynamic_prefixes, list) else [],
        innate_skill_categories=innate_map if isinstance(innate_map, dict) else {},
        known_tool_names=set(known) if isinstance(known, list) else set(),
        unresolved=unresolved if isinstance(unresolved, dict) else {"allow": [], "deny": []},
        provenance=provenance if isinstance(provenance, dict) else {},
    )


def tool_weights() -> dict[str, int]:
    raw = _load_json_artifact("tool-weights.json")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        if isinstance(v, (int, float)):
            out[str(k)] = int(v)
    return out


class _GrantMap(dict):
    """``allowlists()``'s flat grant map plus DEGRADABLE attribution.

    The dict is the verdict authority (§3.6.3 step 1); ``reasons`` is a
    separate, optional input.  A monkeypatched ``allowlists`` returning a
    plain dict simply has no attribution — the verdict must not change.
    """

    def __init__(self, *args: Any, reasons: dict[str, dict[str, str]] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reasons: dict[str, dict[str, str]] = reasons or {}


def allowlists() -> dict[str, list[str]]:
    src = allowlist_source()
    if src.state != "ok":
        return _GrantMap()
    out = _GrantMap()
    reasons_out: dict[str, dict[str, str]] = {}
    for agent_id, info in (src.agents or {}).items():
        if not isinstance(info, dict):
            out[str(agent_id)] = []
            reasons_out[str(agent_id)] = {}
            continue
        agent = {
            "allow": info.get("allow", []),
            "deny": info.get("deny", []),
            "innate": info.get("innate", []),
        }
        g, r, _c = effective_agent_tools(agent, src)
        out[str(agent_id)] = sorted(g)
        # W2: the reasons computed WITH innate expansion are the attribution
        # source; `AllowSource.grant_reason` alone re-derives from `allow`
        # only and loses the `category:todo` grants of innate skills.
        reasons_out[str(agent_id)] = r
    out.reasons = reasons_out
    return out


def _weight_for(tool_name: str) -> int:
    w = tool_weights().get(tool_name)
    if isinstance(w, int) and w > 0:
        return w
    return FALLBACK_WEIGHT


def _weight_section(
    weights: list[int],
    artifact_empty: bool,
    scope: str = "this project section",
) -> tuple[str, list[str]]:
    """§6.1 — the heading and the disclosure for ONE section of the digest.

    The heading is decided by the SECTION's rendered rows: a section whose
    rows all sit on the 230 fallback cannot be sorted by a weight that does
    not discriminate.  The disclosure's VARIANT is decided by the ARTIFACT's
    own emptiness — the "currently empty" claim and its trigger must agree
    — while the counts it quotes are the SECTION's own rows, and the scope
    is stated so the count describes exactly what it counts.
    """
    measured = sum(1 for w in weights if w != FALLBACK_WEIGHT)
    fallback_rows = len(weights) - measured
    heading = FALLBACK_WEIGHT_HEADING if measured == 0 else WEIGHT_HEADING
    if artifact_empty:
        disclosure = [
            "> **Weight disclosure:** the per-tool schema-weight artifact (`tool-weights.json`) is currently empty.",
            "> All tools in this section use the 230-token average fallback (deterministic; `phase3-plan.md` §3.3).",
            "> While the fallback is in effect the Weight column is constant (230) and the ranking is frequency-only.",
        ]
    else:
        row_phrase = "row uses" if fallback_rows == 1 else "rows use"
        disclosure = [
            "> **Weight disclosure:** the per-tool schema-weight artifact (`tool-weights.json`) carries measured "
            f"weights for {measured} of {len(weights)} rendered rows in {scope}; "
            f"{fallback_rows} {row_phrase} the 230-token average fallback (deterministic; `phase3-plan.md` §3.3).",
            "> Rows on the fallback carry a constant Weight (230); their ranking contribution is frequency-only.",
        ]
    return heading, disclosure


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


def effective_allow(agent: Any, src: AllowSource) -> Any:
    allow = _attr(agent, "allow", None)
    innate = _attr(agent, "innate", None) or []
    if not innate or allow is None:
        return allow
    extra: list[str] = []
    for skill in innate:
        for cat in src.innate_skill_categories.get(skill, []):
            if cat in (src.privileged_categories or []):
                continue
            if cat not in (allow or []) and cat not in extra:
                extra.append(cat)
    if extra:
        return [*(allow or []), *extra]
    return allow


def effective_agent_tools(agent: Any, src: AllowSource) -> tuple[set[str], dict[str, str], set[str]]:
    allow = effective_allow(agent, src)
    deny = _attr(agent, "deny", None) or []
    granted: set[str] = set()
    reasons: dict[str, str] = {}
    granted_categories: set[str] = set()
    if not allow:
        granted = {t for cat, tools in (src.categories or {}).items()
                   if cat not in (src.privileged_categories or [])
                   for t in (tools or [])}
        reasons = {t: "inherit-all (empty allow; non-privileged default universe)" for t in granted}
        granted_categories = {cat for cat in src.category_keys
                             if cat not in (src.privileged_categories or [])}
    else:
        for entry in allow:
            es = str(entry)
            if es in src.category_keys:
                granted_categories.add(es)
                for t in (src.categories.get(es) or []):
                    granted.add(t)
                    reasons.setdefault(t, f"category:{es}")
            else:
                granted.add(es)
                reasons.setdefault(es, "literal")
    denied: set[str] = set()
    for entry in (deny or []):
        es = str(entry)
        if es in src.category_keys:
            denied |= set(src.categories.get(es) or [])
        else:
            denied.add(es)
    granted -= denied
    for t in denied:
        reasons.pop(t, None)
    return granted, reasons, granted_categories


def grant_verdict(
    tool: str,
    agent_id: str,
    granted: set[str],
    src: AllowSource,
    reasons: dict[str, str] | None = None,
) -> tuple[str, str]:
    # 0. deny gate precedes every grant branch
    hit = src.deny_entry_for(agent_id, tool)
    if hit is not None and tool not in granted:
        owner = src.owned_category.get(tool)
        via = ""
        if owner is not None and owner in src.granted_categories.get(agent_id, set()):
            via = f"category {owner} allowed, "
        return "no", f"denied: {via}deny wins (deny entry `{hit}`)"
    # 1. flat effective-grant map is the "yes" authority.
    # LOAD-BEARING ORDER: this step MUST be tested before the degraded-artifact
    # gate below. Moving the gate ahead of step 1 silently turns both pinned
    # legs into "unknown" and REDS two assertions that must keep passing
    # unchanged (tests/unit/services/test_tool_usage_digest_determinism.py:541, :544).
    if tool in granted:
        # The reason is SEPARATE and DEGRAABLE: the effective-grant map's own
        # attribution (which includes innate-skill categories) wins when it
        # is available; the artifact-derived fallback covers monkeypatched
        # flat maps that carry no attribution.  Neither path changes verdict.
        attribution = (reasons or {}).get(tool)
        return "yes", attribution if attribution else src.grant_reason(agent_id, tool)
    # 2. degraded artifact
    if src.state != "ok":
        return "unknown", f"artifact {src.state} (allow-list dimension disabled)"
    # 3. agent coverage
    if src.agents and agent_id not in src.agents:
        return "unknown", "agent not present in the artifact (custom/DB-seeded agent)"
    # 4. inherit-all
    if src.inherits_all(agent_id):
        owner = src.owned_category.get(tool)
        if owner is not None:
            if owner in (src.privileged_categories or []):
                return "no", f"privileged category {owner} - never part of the default universe"
            return "yes", "inherit-all (empty allow; non-privileged default universe)"
        if src.is_mcp_shaped(tool):
            return "unknown", "MCP: membership is call-time state (DB/config server list; the category stub has no static members)"
        if src.is_dynamic_prefix_shaped(tool):
            return "unknown", "plane_ prefix sweep: the swept set is call-time state"
        return "unknown", "name not attributed to any static category - the instance's runtime category map may still contain it (G4)"
    # 5. literal entry
    if tool in src.literal_grants.get(agent_id, set()):
        return "yes", "literal"
    # 6. statically owned
    owner = src.owned_category.get(tool)
    if owner is not None:
        if owner in src.granted_categories.get(agent_id, set()):
            return "yes", f"category:{owner}"
        return "no", f"owned by category {owner} - not swept into the allowed category (ownership guard)"
    # 7. mcp-shaped
    if src.is_mcp_shaped(tool):
        return "unknown", "MCP: membership is call-time state (DB/config server list; the category stub has no static members)"
    # 8. plane-shaped
    if src.is_dynamic_prefix_shaped(tool):
        if src.category_granted(agent_id, "plane") or src.inherits_all(agent_id):
            return "unknown", "plane_ prefix sweep: the swept set is call-time state"
        return "no", "not granted (allow expansion minus deny; deny wins)"
    # 9. not in known vocabulary
    if tool not in src.known_tool_names:
        return "unknown", "name not in the known vocabulary - not statically decidable"
    # 10. known, not granted
    return "no", "not granted (allow expansion minus deny; deny wins)"


def rollup(engine: Engine, run_date: str | None = None) -> dict[str, Any]:
    rd: date = _parse_run_date(run_date)
    last24_day, week_days = _window_days(rd)
    week_strs: set[str] = {_day_str(d) for d in week_days}
    last24_str: str = _day_str(last24_day)

    with Session(engine) as session:
        stmt = select(ToolUsageDaily).where(ToolUsageDaily.day.in_(sorted(week_strs)))
        rows: list[ToolUsageDaily] = list(session.exec(stmt))

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

    wl: dict[str, int] = tool_weights()
    # N-2: the fallback disclosure's TRIGGER is the ARTIFACT's own emptiness
    # (what the artifact carries), NOT the rendered rows (what the digest
    # happens to show). A non-empty artifact whose keys match no rendered
    # tool must never be described as "currently empty".
    artifact_empty = not wl
    al: dict[str, list[str]] = allowlists()
    # W2: the flat map carries its attribution alongside it. A plain-dict
    # monkeypatch has none — the reason degrades, the verdict does not.
    grant_reasons: dict[str, dict[str, str]] = getattr(al, "reasons", None) or {}
    src = allowlist_source()

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
                granted_set = set(al.get(agent, []))
                verdict, reason = grant_verdict(
                    tool, agent, granted_set, src, reasons=grant_reasons.get(agent, {})
                )
                agents_out.append({
                    "agent_id": agent,
                    "calls_24h": a["calls_24h"],
                    "calls_7d": a["calls_7d"],
                    "allow_listed": verdict == "yes",
                    "allow_listed_detail": {"verdict": verdict, "reason": reason},
                })
            verdicts = [ao["allow_listed_detail"]["verdict"] for ao in agents_out]
            if not verdicts:
                cell = "yes" if tool in tool_allowed_agents else "no"
            else:
                uniq = set(verdicts)
                if len(uniq) == 1:
                    cell = verdicts[0]
                else:
                    from collections import Counter
                    c = Counter(verdicts)
                    cell = f"mixed(yes={c.get('yes',0)} no={c.get('no',0)} unknown={c.get('unknown',0)})"
            tool_list.append({
                "tool_name": tool,
                "weight": weight,
                "calls_24h": t["calls_24h"],
                "calls_7d": t["calls_7d"],
                "score": score,
                "allow_listed_any": tool in tool_allowed_agents,
                "allowed_agents": sorted(tool_allowed_agents.get(tool, set())),
                "agents": agents_out,
                "allow_listed_cell": cell,
            })
        tool_list.sort(key=lambda x: (-x["score"], x["tool_name"]))
        # §6.1 — the heading and the disclosure are computed PER PROJECT
        # SECTION: a section's heading and its counts describe THAT section's
        # rows, never the digest's global totals. A section whose rows are
        # all on the fallback must not carry a heading that claims a
        # schema-weight sort.
        section_heading, section_disclosure = _weight_section(
            [t["weight"] for t in tool_list], artifact_empty
        )
        project_list.append({
            "project_id": proj,
            "tools": tool_list,
            "weight_heading": section_heading,
            "weight_disclosure": section_disclosure,
        })

    # §6.1 — the heading and its permanent disclosure are COMPUTED from the
    # rendered rows, never hard-coded in the renderer. Score stays
    # `weight × calls_7d` and the six-column table is unchanged. The
    # digest-level pair below is the WHOLE-document summary; the RENDERED
    # heading and disclosure are the per-section ones computed above.
    rendered_weights = [t["weight"] for p in project_list for t in p["tools"]]
    weight_heading, weight_disclosure = _weight_section(
        rendered_weights, artifact_empty, scope="this digest"
    )

    all_allowed_tools: set[str] = set()
    for agent, tools in al.items():
        for t in tools:
            all_allowed_tools.add(t)

    zero_near_zero: list[dict[str, Any]] = []
    if src.state == "ok":
        for tool in sorted(all_allowed_tools):
            present_calls: dict[str, int] = {}
            for p in project_list:
                for t in p["tools"]:
                    if t["tool_name"] == tool:
                        present_calls[p["project_id"]] = t["calls_7d"]
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
        "allowlist_state": src.state,
        "allowlist_schema_version": src.schema_version,
        "allowlist_rejection_reason": src.rejection_reason,
        "weight_heading": weight_heading,
        "weight_disclosure": weight_disclosure,
        "unresolved": src.unresolved,
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

    state = rollup_data.get("allowlist_state", "ok")
    if state != "ok":
        lines.append(f"> ⚠️ ALLOW-LIST DIMENSION DISABLED — artifact state: {state}")
        if state == "rejected":
            found = rollup_data.get("allowlist_schema_version")
            found_str = str(found) if found else "none"
            lines.append(f"> ⚠️ ARTIFACT SCHEMA REJECTED (found: {found_str}, required: 2)")
            rejection_reason = rollup_data.get("allowlist_rejection_reason")
            if rejection_reason:
                lines.append(f"> rejection reason: {rejection_reason}")
        lines.append("")

    for p in rollup_data["projects"]:
        lines.append(f"## Project: {p['project_id']}")
        lines.append("")
        # §6.1: the heading is data, not a literal in the renderer, and it is
        # PER SECTION: a section whose rows are all on the fallback must not
        # carry a heading that claims a schema-weight sort. Hand-built
        # rollups (tests) without the per-section fields degrade to the
        # honest per-section fallback computed from that section's rows.
        heading = p.get("weight_heading")
        if not heading:
            weights = [t["weight"] for t in p["tools"]]
            heading = WEIGHT_HEADING if any(w != FALLBACK_WEIGHT for w in weights) else FALLBACK_WEIGHT_HEADING
        lines.append(heading)
        # The disclosure is per section too: its counts describe THIS
        # section's rows and the disclosure says so.
        disclosure = p.get("weight_disclosure")
        if disclosure is None:
            disclosure = rollup_data.get("weight_disclosure") or []
        for disclosure_line in disclosure:
            lines.append(disclosure_line)
        lines.append("")
        lines.append("| Tool | Weight | Calls 24h | Calls 7d | Score | Allow-listed |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for t in p["tools"]:
            al = t.get("allow_listed_cell", "yes" if t["allow_listed_any"] else "no")
            lines.append(f"| {t['tool_name']} | {t['weight']} | {t['calls_24h']} | {t['calls_7d']} | {t['score']} | {al} |")
        lines.append("")
        lines.append("### Per-agent breakdown")
        lines.append("")
        for t in p["tools"]:
            if not t["agents"]:
                continue
            lines.append(f"#### {t['tool_name']}")
            for a in t["agents"]:
                detail = a.get("allow_listed_detail") or {}
                verdict = detail.get("verdict", "yes" if a.get("allow_listed") else "no")
                reason = detail.get("reason", "no reason available")
                if verdict == "yes":
                    if reason and reason != "no reason available":
                        marker = f" (allow-listed) — {reason}"
                    else:
                        marker = " (allow-listed)"
                elif verdict == "no":
                    if reason and reason != "no reason available":
                        marker = f" (not allow-listed — {reason})"
                    else:
                        marker = " (not allow-listed)"
                else:
                    if reason and reason != "no reason available":
                        marker = f" (allow-list unknown — {reason})"
                    else:
                        marker = " (allow-list unknown)"
                lines.append(f"- {a['agent_id']}: 24h={a['calls_24h']} 7d={a['calls_7d']}{marker}")
            lines.append("")
        lines.append("")

    lines.append("## Zero / near-zero candidates (allow-listed, ≤3 calls in 7d)")
    lines.append("")
    for z in rollup_data["zero_near_zero"]:
        agents = ", ".join(z["allowed_agents"]) if z["allowed_agents"] else "(none)"
        lines.append(f"- {z['tool_name']}: calls_7d={z['calls_7d']} — allowed by: {agents}")
    lines.append("")

    # §3.6.5 rule (2): the no-silent-drop ledger is surfaced in the notes
    # footer. This is the document TAIL (after the final level-2 heading), so
    # every unresolved entry's AGENT and ENTRY are named — never dropped.
    unresolved = rollup_data.get("unresolved") or {}
    allow_entries = unresolved.get("allow", []) or []
    deny_entries = unresolved.get("deny", []) or []
    lines.append("## Notes")
    lines.append("")
    lines.append(
        f"unresolved allow entries: {len(allow_entries)} · "
        f"unresolved deny entries: {len(deny_entries)}"
    )
    if allow_entries or deny_entries:
        named = []
        for e in allow_entries:
            named.append(f"{e.get('agent')}: {e.get('entry')}")
        for e in deny_entries:
            named.append(f"{e.get('agent')}: {e.get('entry')}")
        lines.append(
            "unresolved entries: " + " · ".join(named)
            + " (in no vocabulary constant; the registry validator warns on it)"
        )
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
    "WEIGHT_HEADING",
    "FALLBACK_WEIGHT_HEADING",
    "NEAR_ZERO_CALLS_7D",
    "DIGEST_ROOT_ENV",
    "ARTIFACT_DIR_ENV",
    "DIGEST_RELATIVE_DIR",
    "ARTIFACT_DIRNAME",
    "DigestRootUnresolved",
    "AllowSource",
    "_verified_install_root",
    "_install_root",
    "_code_root",
    "_artifact_dir",
    "digest_root",
    "allowlist_source",
    "allowlists",
    "effective_allow",
    "effective_agent_tools",
    "grant_verdict",
    "rollup",
    "render_markdown",
    "digest_path",
    "write_digest",
    "tool_weights",
]

