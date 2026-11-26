"""Schema-2 artifact gate + loudness + no-silent-drop suite — spec v4 §3.9.4 (8 nodes).

The digest reads a COMMITTED artifact.  This suite pins the two halves of
that contract:

* the GATE — a payload whose ``schema_version`` is missing or ≠ 2 (the
  old schema-1 shape), or whose per-agent object lacks the ``deny`` key,
  is REJECTED LOUDLY (banner + ``ERROR`` log + every cell ``unknown`` +
  the zero/near-zero section disabled).  A missing/unreadable artifact
  degrades the same way: ``unknown`` everywhere, NEVER a false ``no``.
* the LEDGER — an allow/deny entry that is in no vocabulary constant is
  PRESERVED as a literal (never dropped, never rewritten), SURFACED in the
  artifact's ``unresolved`` block and in the digest's notes footer, and
  decided PER CASE: an unresolved ALLOW entry is a literal grant for the
  agent that carries it (``yes``/``literal``) and ``unknown`` for every
  other agent; an unresolved DENY entry is ``no`` for the denying agent
  and ``unknown`` for the others.

The MCP / dynamic-prefix rows are call-time state, so they render
``unknown`` — never ``yes``, never ``no``.

HERMETIC (binding): the reader is pointed at ``tmp_path`` through the
documented test-only seam ``ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR`` (unset in
production); the ledger fixture is written to ``tmp_path``; the digest is
computed over a throwaway SQLite file under ``tmp_path``.  No live
artifact is modified, no live install is touched, and no database other
than the throwaway SQLite file is contacted.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

from daemon.repositories.tool_usage.models import ToolUsageDaily
from daemon.services import tool_usage_digest as tud

#: The run date (D) and its complete previous UTC day (D-1).
RUN_DATE = "2026-11-21"
DAY_D1 = "2026-11-20"
PROJECT = "p-schema"


# ── fixture builders ───────────────────────────────────────────────────────


def _row(
    day: str,
    project_id: str,
    tool_name: str,
    agent_id: str,
    call_count: int,
) -> ToolUsageDaily:
    return ToolUsageDaily(
        day=day,
        project_id=project_id,
        tool_name=tool_name,
        agent_id=agent_id,
        call_count=call_count,
        updated_at=f"{day}T00:00:00+00:00",
    )


def _sqlite_engine(tmp_path: Path, name: str = "artifact-schema.db") -> Engine:
    """File-backed SQLite (never ``:memory:`` — several Sessions must see
    one database).  Only the ledger table is created."""
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(engine, tables=[ToolUsageDaily.__table__])
    return engine


def _seed(engine: Engine, rows: list[ToolUsageDaily]) -> None:
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def _write_artifact(artifact_dir: Path, payload: dict[str, Any]) -> Path:
    """Write a schema payload into the reader's artifact dir."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "allowlists.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _point_reader_at(monkeypatch: pytest.MonkeyPatch, artifact_dir: Path) -> None:
    """Point the READER at ``artifact_dir`` through the documented
    test-only seam (``ARTIFACT_DIR_ENV``; unset in production)."""
    monkeypatch.setenv(tud.ARTIFACT_DIR_ENV, str(artifact_dir))


def _corpus_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the COMMITTED artifact (source mode) — no seam, no monkeypatching
    of the readers."""
    monkeypatch.delenv(tud.ARTIFACT_DIR_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


# ── helpers on the rendered markdown ───────────────────────────────────────


#: The loudness markers a banner line may carry.  A banner is either a
#: block-quoted line or a plain line carrying one of these markers.
_BANNER_MARKERS = (
    "⚠️",
    "ALLOW-LIST DIMENSION DISABLED",
    "ARTIFACT SCHEMA REJECTED",
)


def _banner_lines(markdown: str) -> list[str]:
    """The banner lines of the digest (block-quoted OR marker-carrying)."""
    return [
        ln
        for ln in markdown.splitlines()
        if ln.lstrip().startswith(">") or any(m in ln for m in _BANNER_MARKERS)
    ]


def _excerpt(text: str, limit: int = 240) -> str:
    """A bounded excerpt — a failure message must stay readable."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + f"… (+{len(flat) - limit} chars)"


def _footer_text(markdown: str) -> str:
    """The document TAIL — the notes-footer surface (§3.6.5 rule 2).

    The notes footer is the LAST thing the digest renders, so the tail
    after the final level-2 heading is where it lives (a ``## Notes``
    section, or plain lines appended under the zero/near-zero section).
    """
    lines = markdown.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert heads, "the digest rendered no level-2 section"
    return "\n".join(lines[heads[-1]:])


def _agent_lines(markdown: str, tool_name: str) -> list[str]:
    """The per-agent bullet lines under ``#### <tool_name>``."""
    lines: list[str] = []
    capture = False
    for line in markdown.splitlines():
        if line == f"#### {tool_name}":
            capture = True
            continue
        if capture:
            if line.startswith("- "):
                lines.append(line)
                continue
            break
    return lines


def _verdicts(data: dict[str, Any], tool_name: str) -> dict[str, str]:
    """``{agent_id: verdict}`` for one tool in the rollup."""
    out: dict[str, str] = {}
    for p in data["projects"]:
        for t in p["tools"]:
            if t["tool_name"] != tool_name:
                continue
            for a in t["agents"]:
                out[a["agent_id"]] = a["allow_listed_detail"]["verdict"]
    return out


def _cell(data: dict[str, Any], tool_name: str) -> str:
    """The rendered table cell (the three-valued summary) for one tool."""
    for p in data["projects"]:
        for t in p["tools"]:
            if t["tool_name"] == tool_name:
                return t["allow_listed_cell"]
    raise AssertionError(f"{tool_name!r} is not in the rollup")


def _unresolvable_entries(payload: dict[str, Any]) -> set[tuple[str, str]]:
    """Re-derive the unresolvable (unclassified) entries of a schema-2
    payload: an entry that is in no category key, in no known tool name and
    carries no dynamic prefix (§3.6.5's definition)."""
    category_keys = set(payload.get("categories", {})) | set(
        payload.get("runtime_only_categories", [])
    )
    known = set(payload.get("known_tool_names", []))
    prefixes = tuple(payload.get("dynamic_prefixes", []))

    def _unresolvable(entry: str) -> bool:
        if entry in category_keys or entry in known:
            return False
        return not any(entry.startswith(p) for p in prefixes)

    out: set[tuple[str, str]] = set()
    for agent, info in payload.get("agents", {}).items():
        for entry in info.get("allow", []):
            if _unresolvable(entry):
                out.add((agent, entry))
        for entry in info.get("deny", []):
            if _unresolvable(entry):
                out.add((agent, entry))
    return out


# ── 48. a schema-1 artifact is REJECTED LOUDLY ────────────────────────────


def test_an_unversioned_artifact_is_rejected_loudly(monkeypatch, tmp_path, caplog):
    """Spec §3.9.4 test 48 — the OLD (schema-1) shape is rejected loudly.

    The old committed shape (``{"agents": {...}, "generated_from_commit":
    ...}``) carries no ``schema_version``.  It must be REJECTED — not
    silently read as a schema-2 payload — with: a banner that NAMES THE
    REQUIRED VERSION, every cell ``unknown``, the zero/near-zero section
    disabled, and an ``ERROR`` log.
    """
    artifact_dir = tmp_path / "artifacts"
    _write_artifact(
        artifact_dir,
        {
            # The OLD committed shape: agents map values are LISTS, and the
            # provenance key is the retired `generated_from_commit`.
            "agents": {"ari": ["bash", "read_file"]},
            "generated_from_commit": "a064fedf49a604f36cdcc3012161fe6cce9f7367",
        },
    )
    _point_reader_at(monkeypatch, artifact_dir)

    caplog.set_level(logging.DEBUG)
    src = tud.allowlist_source()

    assert src.state == "rejected"

    engine = _sqlite_engine(tmp_path)
    _seed(engine, [_row(DAY_D1, PROJECT, "read_file", "ari", 3)])
    data = tud.rollup(engine, RUN_DATE)
    md = tud.render_markdown(data, "FIXED")

    # every cell unknown — never a decided verdict
    for p in data["projects"]:
        for t in p["tools"]:
            assert t["allow_listed_cell"] == "unknown"
            for a in t["agents"]:
                assert a["allow_listed_detail"]["verdict"] == "unknown"
                assert a["allow_listed"] is False

    # the zero/near-zero section is disabled
    assert data["zero_near_zero"] == []

    banners = _banner_lines(md)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]

    problems: list[str] = []
    if not banners:
        problems.append("no banner was rendered")
    else:
        banner = " ".join(banners)
        if "schema" not in banner.lower():
            problems.append(f"the banner does not name the schema: {_excerpt(banner)!r}")
        if not re.search(r"(?<!\d)2(?!\d)", banner):
            problems.append(
                f"the banner does not name the REQUIRED version 2: {_excerpt(banner)!r}"
            )
    if not errors:
        problems.append("no ERROR log record was emitted")
    assert not problems, "; ".join(problems)


# ── 49. the committed schema-2 artifact loads ──────────────────────────────


def test_a_schema_2_artifact_is_accepted(monkeypatch):
    """Spec §3.9.4 test 49 — the committed artifact loads as schema 2."""
    _corpus_reader(monkeypatch)

    src = tud.allowlist_source()

    assert src.state == "ok"
    assert src.schema_version == 2
    assert src.agents, "the committed artifact carries no agents"
    assert src.known_tool_names, "the committed artifact carries no known-name vocabulary"
    # the runtime-only category keys are CATEGORIES, not tool names
    assert "mcp" in src.runtime_only_categories
    assert "plane" in src.runtime_only_categories


# ── 50. a MISSING deny list is a rejection, not an empty deny ─────────────


def test_a_missing_deny_list_is_not_read_as_an_empty_deny_list(monkeypatch, tmp_path):
    """Spec §3.9.4 test 50 — the hole the version gate closes.

    A schema-2 payload whose per-agent object LACKS ``deny`` is REJECTED.
    It is never silently read as an empty deny list: a silent empty deny
    would DECIDE the row, while a rejected artifact renders ``unknown``.
    """
    artifact_dir = tmp_path / "artifacts"
    _write_artifact(
        artifact_dir,
        {
            "schema_version": 2,
            "agents": {
                # NO "deny" key at all — the structural hole.
                "ari": {"allow": ["filesystem"], "innate": []},
            },
            "categories": {"filesystem": ["read_file"]},
            "runtime_only_categories": [],
            "privileged_categories": [],
            "dynamic_prefixes": [],
            "innate_skill_categories": {},
            "known_tool_names": ["read_file"],
            "unresolved": {"allow": [], "deny": []},
            "provenance": {},
        },
    )
    _point_reader_at(monkeypatch, artifact_dir)

    src = tud.allowlist_source()

    assert src.state == "rejected"
    assert src.schema_version == 2

    engine = _sqlite_engine(tmp_path)
    _seed(engine, [_row(DAY_D1, PROJECT, "read_file", "ari", 3)])
    data = tud.rollup(engine, RUN_DATE)

    for p in data["projects"]:
        for t in p["tools"]:
            for a in t["agents"]:
                assert a["allow_listed_detail"]["verdict"] == "unknown"
                assert a["allow_listed"] is False


# ── 51. MCP rows render unknown — never yes, never no ─────────────────────


def test_mcp_rows_render_unknown_never_yes_or_no(monkeypatch, tmp_path):
    """Spec §3.9.4 test 51 — the MCP runtime class is call-time state.

    An ``mcp_<server>_<tool>`` row for an agent that allows the ``mcp``
    CATEGORY renders ``unknown``: the server list is call-time state, so
    neither ``yes`` nor ``no`` is decidable.
    """
    _corpus_reader(monkeypatch)

    src = tud.allowlist_source()
    assert src.state == "ok"
    assert "mcp" in src.granted_categories.get("explorer", set()), (
        "the corpus no longer allows the mcp category to explorer — "
        "the probe needs an mcp-allowing agent"
    )
    granted = set(tud.allowlists().get("explorer", []))

    verdict, reason = tud.grant_verdict("mcp_context7_query-docs", "explorer", granted, src)
    assert verdict == "unknown"
    assert "MCP" in reason

    engine = _sqlite_engine(tmp_path)
    _seed(engine, [_row(DAY_D1, PROJECT, "mcp_context7_query-docs", "explorer", 4)])
    data = tud.rollup(engine, RUN_DATE)
    md = tud.render_markdown(data, "FIXED")

    assert _cell(data, "mcp_context7_query-docs") == "unknown"
    assert _verdicts(data, "mcp_context7_query-docs") == {"explorer": "unknown"}
    lines = _agent_lines(md, "mcp_context7_query-docs")
    assert lines, "the mcp row was not rendered"
    assert all("(allow-list unknown" in ln for ln in lines)


# ── 52. the plane runtime class vs a literal grant ────────────────────────


def test_plane_runtime_rows_render_unknown_and_literal_grants_render_yes(monkeypatch, tmp_path):
    """Spec §3.9.4 test 52 — a literal grant is exact; the sweep is not.

    The SAME ``plane_*`` name renders ``yes`` (``literal``) for the agent
    that literally allows it (``project-manager`` allows 28 ``plane_*``
    entries) and ``unknown`` for an agent that does not — the swept set is
    call-time state.

    Corpus note: NO agent in the live corpus allows the runtime-only
    ``plane`` CATEGORY (the category is granted at runtime, not
    statically), so the corpus-driven "not literally allowed" leg is the
    inherit-all agent (``watcher``).  The category-grant leg — the one
    APPROVER-B2b names — is driven by a synthetic artifact below, because
    the corpus cannot supply it.
    """
    _corpus_reader(monkeypatch)

    src = tud.allowlist_source()
    assert src.state == "ok"
    allowed = tud.allowlists()

    # (1) the LITERAL grant — exact for the agent that carries it
    pm_granted = set(allowed.get("project-manager", []))
    assert "plane_list_projects" in src.literal_grants.get("project-manager", set())
    verdict, reason = tud.grant_verdict("plane_list_projects", "project-manager", pm_granted, src)
    assert verdict == "yes"
    assert reason == "literal"

    # (2) the SAME name for an agent that does NOT literally allow it
    watcher_granted = set(allowed.get("watcher", []))
    assert "plane_list_projects" not in src.literal_grants.get("watcher", set())
    verdict, reason = tud.grant_verdict("plane_list_projects", "watcher", watcher_granted, src)
    assert verdict == "unknown"
    assert "plane_" in reason

    # (3) the CATEGORY-grant leg (APPROVER-B2b) — fixture-driven
    fixture_dir = tmp_path / "artifacts"
    _write_artifact(
        fixture_dir,
        {
            "schema_version": 2,
            "agents": {
                "plane_agent": {"allow": ["plane"], "deny": [], "innate": []},
                "no_plane_agent": {"allow": ["filesystem"], "deny": [], "innate": []},
            },
            "categories": {"filesystem": ["read_file"]},
            "runtime_only_categories": ["mcp", "plane"],
            "privileged_categories": [],
            "dynamic_prefixes": ["plane_"],
            "innate_skill_categories": {},
            "known_tool_names": ["read_file"],
            "unresolved": {"allow": [], "deny": []},
            "provenance": {},
        },
    )
    _point_reader_at(monkeypatch, fixture_dir)

    fixture_src = tud.allowlist_source()
    assert fixture_src.state == "ok"
    fixture_allowed = tud.allowlists()

    verdict, reason = tud.grant_verdict(
        "plane_sync_project",
        "plane_agent",
        set(fixture_allowed.get("plane_agent", [])),
        fixture_src,
    )
    assert verdict == "unknown", "the plane_ sweep is call-time state — never a confident no"
    assert "plane_" in reason


# ── 53. the unclassified population is REPORTED, not dropped ───────────────


def test_the_unclassified_population_is_reported_not_dropped(monkeypatch, tmp_path):
    """Spec §3.9.4 test 53 — the no-silent-drop rule (§3.6.5).

    The measured population at the pinned tree: ALLOW side 0, DENY side 1
    (``maintenancer`` / ``git_commit`` — a name in no vocabulary constant,
    which the registry validator warns on).  The artifact's ``unresolved``
    block carries it and the digest's notes footer NAMES it: nothing is
    dropped silently.
    """
    _corpus_reader(monkeypatch)

    src = tud.allowlist_source()
    assert src.state == "ok"

    unresolved = src.unresolved
    assert isinstance(unresolved, dict)
    assert unresolved["allow"] == []
    assert len(unresolved["deny"]) == 1
    entry = unresolved["deny"][0]
    assert entry["agent"] == "maintenancer"
    assert entry["entry"] == "git_commit"

    engine = _sqlite_engine(tmp_path)
    # The seeded rows deliberately do NOT name the unclassified entry, so a
    # mention of it in the digest can only come from the notes footer.
    _seed(engine, [_row(DAY_D1, PROJECT, "read_file", "explorer", 3)])
    data = tud.rollup(engine, RUN_DATE)
    md = tud.render_markdown(data, "FIXED")

    footer = _footer_text(md)
    problems: list[str] = []
    if "unresolved" not in footer.lower():
        problems.append(
            f"the notes footer does not report the unresolved ledger: {_excerpt(footer)!r}"
        )
    if "git_commit" not in footer:
        problems.append(f"the notes footer does not name the entry: {_excerpt(footer)!r}")
    if "maintenancer" not in footer:
        problems.append(f"the notes footer does not name the agent: {_excerpt(footer)!r}")
    assert not problems, "; ".join(problems)


# ── 54. an unresolvable entry is PRESERVED and SURFACED ───────────────────


def test_an_unresolvable_allow_entry_is_preserved_as_a_literal_and_surfaced(monkeypatch, tmp_path):
    """Spec §3.9.4 test 54 — the fixture-driven no-silent-drop probe.

    The live corpus CANNOT drive the ALLOW side (the measured unclassified
    ALLOW population is 0), so the fixture is mandatory: a synthetic
    schema-2 payload carrying an allow entry and a deny entry that are in
    NO vocabulary constant, loaded through the REAL reader (no monkeypatch
    of the readers).

    (a) PRESERVED — the entries survive as literals: the allow entry is a
        literal grant of that one name, the deny entry a literal deny.
    (b) SURFACED — they appear in the artifact's ``unresolved`` ledger and
        in the digest's notes footer, and the cell for that tool renders
        the PER-CASE expectation, never a confident "no": the carrying
        agent gets ``yes``/``literal`` (ALLOW) or ``no`` (DENY), every
        other agent gets ``unknown``.
    (c) COMPLETE — the fixture's unresolvable set equals the ``unresolved``
        block exactly (no entry lost, no entry invented).
    """
    artifact_dir = tmp_path / "artifacts"
    payload = {
        "schema_version": 2,
        "agents": {
            # the ALLOW-side unresolvable entry
            "alpha": {"allow": ["totally_unknown_tool"], "deny": [], "innate": []},
            # the DENY-side unresolvable entry
            "beta": {"allow": ["filesystem"], "deny": ["git_commit"], "innate": []},
            # a plain agent, to pin the "unknown for every OTHER agent" leg
            "gamma": {"allow": ["filesystem"], "deny": [], "innate": []},
        },
        "categories": {"filesystem": ["read_file"]},
        "runtime_only_categories": ["mcp", "plane"],
        "privileged_categories": [],
        "dynamic_prefixes": ["plane_"],
        "innate_skill_categories": {},
        # neither unresolvable name is in the known-name vocabulary
        "known_tool_names": ["read_file"],
        "unresolved": {
            "allow": [{"agent": "alpha", "entry": "totally_unknown_tool"}],
            "deny": [{"agent": "beta", "entry": "git_commit"}],
        },
        "provenance": {},
    }
    _write_artifact(artifact_dir, payload)
    _point_reader_at(monkeypatch, artifact_dir)

    src = tud.allowlist_source()
    assert src.state == "ok"

    allowed = tud.allowlists()

    # (a) PRESERVED as literals — never dropped, never rewritten
    assert src.literal_grants["alpha"] == {"totally_unknown_tool"}
    assert src.deny_entry_for("beta", "git_commit") == "git_commit"
    assert "totally_unknown_tool" in set(allowed["alpha"])
    assert "git_commit" not in set(allowed["beta"])

    # (b) SURFACED — the ledger, the per-case verdicts, the rendered rows
    assert src.unresolved == payload["unresolved"]

    verdict, reason = tud.grant_verdict(
        "totally_unknown_tool", "alpha", set(allowed["alpha"]), src
    )
    assert (verdict, reason) == ("yes", "literal")
    verdict, reason = tud.grant_verdict(
        "totally_unknown_tool", "gamma", set(allowed["gamma"]), src
    )
    assert verdict == "unknown", "never a confident no for an agent that carries no grant"
    verdict, reason = tud.grant_verdict("git_commit", "beta", set(allowed["beta"]), src)
    assert verdict == "no", "a literal deny is exact — deny wins"
    verdict, reason = tud.grant_verdict("git_commit", "alpha", set(allowed["alpha"]), src)
    assert verdict == "unknown"

    engine = _sqlite_engine(tmp_path)
    _seed(
        engine,
        [
            _row(DAY_D1, PROJECT, "totally_unknown_tool", "alpha", 5),
            _row(DAY_D1, PROJECT, "totally_unknown_tool", "gamma", 2),
            _row(DAY_D1, PROJECT, "git_commit", "beta", 1),
            _row(DAY_D1, PROJECT, "git_commit", "alpha", 1),
        ],
    )
    data = tud.rollup(engine, RUN_DATE)
    md = tud.render_markdown(data, "FIXED")

    assert _verdicts(data, "totally_unknown_tool") == {"alpha": "yes", "gamma": "unknown"}
    assert _verdicts(data, "git_commit") == {"beta": "no", "alpha": "unknown"}

    allow_cell = _cell(data, "totally_unknown_tool")
    assert "yes=1" in allow_cell and "unknown=1" in allow_cell and "no=0" in allow_cell
    deny_cell = _cell(data, "git_commit")
    assert "no=1" in deny_cell and "unknown=1" in deny_cell and "yes=0" in deny_cell

    alpha_lines = [ln for ln in _agent_lines(md, "totally_unknown_tool") if ln.startswith("- alpha")]
    assert alpha_lines and "(allow-listed) — literal" in alpha_lines[0]
    gamma_lines = [ln for ln in _agent_lines(md, "totally_unknown_tool") if ln.startswith("- gamma")]
    assert gamma_lines and "(allow-list unknown" in gamma_lines[0]

    footer = _footer_text(md)
    problems: list[str] = []
    if "totally_unknown_tool" not in footer:
        problems.append(
            f"the notes footer does not name the allow entry: {_excerpt(footer)!r}"
        )
    if "git_commit" not in footer:
        problems.append(f"the notes footer does not name the deny entry: {_excerpt(footer)!r}")
    assert not problems, "; ".join(problems)

    # (c) COMPLETE — the ledger matches the fixture's unresolvable set
    expected = _unresolvable_entries(payload)
    actual = {
        (e["agent"], e["entry"]) for e in src.unresolved.get("allow", [])
    } | {(e["agent"], e["entry"]) for e in src.unresolved.get("deny", [])}
    assert expected == {
        ("alpha", "totally_unknown_tool"),
        ("beta", "git_commit"),
    }, "the fixture's own unresolvable set changed"
    assert actual == expected, "the unresolved ledger lost or invented an entry"


# ── 55. a MISSING artifact degrades to unknown — never a false no ─────────


def test_a_missing_artifact_degrades_to_unknown_not_no(monkeypatch, tmp_path):
    """Spec §3.9.4 test 55 — an absent artifact disables the dimension.

    Every cell renders ``unknown`` (never a false ``no``) and the
    degradation banner is present.
    """
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)  # EMPTY — no allowlists.json
    _point_reader_at(monkeypatch, artifact_dir)

    src = tud.allowlist_source()
    assert src.state == "absent"

    engine = _sqlite_engine(tmp_path)
    _seed(engine, [_row(DAY_D1, PROJECT, "read_file", "ari", 3)])
    data = tud.rollup(engine, RUN_DATE)
    md = tud.render_markdown(data, "FIXED")

    for p in data["projects"]:
        for t in p["tools"]:
            assert t["allow_listed_cell"] == "unknown"
            for a in t["agents"]:
                assert a["allow_listed_detail"]["verdict"] == "unknown"
                assert a["allow_listed"] is False

    banners = _banner_lines(md)
    assert banners, "no degradation banner was rendered"
    assert "allow-list" in " ".join(banners).lower()
