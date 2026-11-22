"""P5 Task 5.5 — the P2/P3/P4 acceptance → check map (Central Maintenance System).

Every row of the phase-5 verification-pass map (``.agents/shared/planning/
maintenance-system/phase5-plan.md`` — "Verification pass — P2/P3/P4
acceptance → check map") gets ONE named, green test node.  Rows whose
coverage already exists in a sibling pin file are CITED here — the map
asserts the cited node id still exists in collection AND passes — instead
of being duplicated.

Ownership (one owner per pin, no duplicates):

* THIS file — the seat grant (BR-10 / P2-BR-8), the POK prohibition
  strings, BR-9 run containment, the BR-12 lane-status table, the BR-13
  writer ladder, BR-14 run-id authority, the PD-4a schema citation, the
  P3 fence-recipe + lane-4 no-write pins, the BR-16b INDEX mechanics,
  the BR-18 live-view root registration, the P4 receipt cross-ref and
  the P1 AC6 failure-mode ledger.
* ``test_maintenancer_report_scrutiny.py`` — the report-ALWAYS
  on-total-failure pin (report-semantics home).
* ``test_maintenancer_fan_in_valve.py`` — the BR-11 event-driven deadline
  legs (fan-in-valve home).
* ``test_maintenancer_team_members_within_team.py`` — the effective
  spawnable-set == 4 pin (team-membership home).
* ``test_maintenancer_skill_versions_consistent.py`` — the manifest /
  frontmatter version-consistency pin (P2 T2.7 owns it; this map only
  CITES it and never edits it).

The phase-5 verification-pass rows owned by a SIBLING pin file (BR-16's
artifact-root equivalence, BR-17's list route, BR-19's FE gear-menu
ordering) are CITED as pointers (``SIBLING_OWNED_ROWS``) — this map never
duplicates their coverage.

Pure file/metadata parsing, plus TWO subprocesses (the BR-14 mint, which
MUST use the named daemon venv interpreter, and the citation sweep).  No
daemon boot, no DB, no live contact.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = PROJECT_ROOT / "agents" / "maintenancer"
SKILLS_DIR = AGENT_DIR / "skills-template"
META_PATH = AGENT_DIR / "meta.json"
RULE_PATH = AGENT_DIR / "rule.md"
WORKFLOW_PATH = AGENT_DIR / "workflow.md"
PLANNING_DIR = PROJECT_ROOT / ".agents" / "shared" / "planning" / "maintenance-system"
DECISIONS_PATH = PLANNING_DIR / "decisions.md"
PHASE1_PATH = PLANNING_DIR / "phase1-plan.md"
PHASE3_PATH = PLANNING_DIR / "phase3-plan.md"
PHASE4_PATH = PLANNING_DIR / "phase4-plan.md"
DISK_BASELINE_PATH = PLANNING_DIR / "disk-lane-baseline-2026-11-18.md"

#: The NAMED daemon venv interpreter (BR-14 / PD-9).  Bare ``python3``
#: FAILS on this host — a test that mints with ``python3`` would
#: false-GREEN/RED for the wrong reason.
DAEMON_VENV_PYTHON = "/home/nea/ensemble-src/.venv/bin/python"

#: The four LITERAL tool names BR-8 grants the seat (NOT the ``"instance"``
#: category — BR-8's whole point).
THE_FOUR_LITERAL_TOOLS: tuple[str, ...] = (
    "spawn_instance",
    "send_message",
    "subtree_messages",
    "subtree_status",
)

#: BR-10 — the deny list is byte-unchanged (the seat stays non-mutating).
EXPECTED_DENY: frozenset[str] = frozenset({"git_commit", "edit_file", "write_file"})

#: The seat's measured grant set.  BR-15's lane-4 orphan listing rides the
#: EXISTING ``instance_read`` surface — a new grant would change this set
#: and redden the no-new-grant pin.
EXPECTED_ALLOW_SET: frozenset[str] = frozenset(
    {
        "system-log",
        "ens-db",
        "knowledge",
        "system_upgrade",
        "system_restart",
        "db",
        "instance_read",
        "instance_ops",
        "wedge-capture",
        "metrics-read",
        "nest-report-read",
        "pok",
        "nest-report",
        "spawn_instance",
        "send_message",
        "subtree_messages",
        "subtree_status",
    }
)

#: BR-10 — the resolved surface must EXCLUDE these names.
BR10_EXCLUDED_NAMES: frozenset[str] = frozenset(
    {
        "terminate_instance",
        "pause_instance",
        "resume_instance",
        "set_instance_tunable",
        "spawn_councilor",
    }
)

#: BR-10 — the resolved surface must exclude the whole ``convene_council*``
#: family (the council quartet).
BR10_EXCLUDED_PREFIXES: tuple[str, ...] = ("convene_council",)

#: BR-16b / BR-17 — the canonical run_id filename shape.
RUN_ID_PATTERN = re.compile(r"^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$")

#: PD-4a — the frozen canonical per-lane result-block field superset.
PD4A_EXPECTED_FIELDS: frozenset[str] = frozenset(
    {
        "lane",
        "status",
        "measured_at",
        "summary",
        "metrics",
        "actions_taken",
        "recommendations",
        "evidence_paths",
    }
)

#: PD-4a — the three recommendation classes.
PD4A_RECOMMENDATION_CLASSES: tuple[str, ...] = (
    "auto_fix_now",
    "needs_emperor_word",
    "route_to_lane",
)

#: The five lane skill bodies (P3) — every one of them must cite PD-4a.
LANE_SKILLS: tuple[str, ...] = (
    "lane-disk-sweep",
    "lane-pok-sweep",
    "lane-skill-check",
    "lane-abnormality-check",
    "lane-metrics-watch",
)

#: The writer skill body (P2 T2.5 owns it — this map only reads it).
WRITER_SKILL = "run-report-writer"

#: The four pin files whose nodes this map CITES (never edits the
#: versions-consistency count-pin — P2 T2.7 solely owns that).
PIN_FILES: tuple[str, ...] = (
    "tests/unit/test_maintenance_acceptance_map.py",
    "tests/unit/test_maintenancer_team_members_within_team.py",
    "tests/unit/test_maintenancer_report_scrutiny.py",
    "tests/unit/test_maintenancer_fan_in_valve.py",
    "tests/unit/test_maintenancer_skill_versions_consistent.py",
)

#: The map itself — row id, human label, owning test-node id.
MAP_ROWS: tuple[tuple[str, str, str], ...] = (
    (
        "BR-10 / P2-BR-8",
        "seat grant: the four LITERAL tool names, deny byte-equality, resolved-surface exclusions",
        "test_br10_allow_contains_the_four_literal_tool_names",
    ),
    (
        "BR-10 spawnable",
        "effective spawnable set == 4 (kb-writer implied by knowledge via TOOL_REQUIRED_AGENTS)",
        "test_effective_spawnable_set_is_four",
    ),
    (
        "P3 lane 2 (POK)",
        "pok_upsert / pok_curate ABSENT from the lane-pok-sweep body; pok_query present",
        "test_pok_write_verbs_are_absent_and_the_query_verb_is_present",
    ),
    (
        "BR-9",
        "run containment clauses in rule.md + workflow.md",
        "test_rule_md_encodes_the_run_containment_clauses",
    ),
    (
        "BR-12",
        "lane-status mapping table (never-dispatched => failed + [incomplete])",
        "test_br12_never_dispatched_maps_to_failed_with_incomplete",
    ),
    (
        "P2 report-ALWAYS",
        "the report is written even when every lane fails",
        "test_workflow_encodes_report_always_on_total_failure",
    ),
    (
        "BR-13",
        "writer ladder L0->L3 + write order run->receipt->INDEX last + DP-1",
        "test_workflow_encodes_the_l0_to_l3_writer_ladder",
    ),
    (
        "BR-11",
        "event-driven lane deadline + valve-applied-once + no polling",
        "test_workflow_encodes_the_event_driven_deadline",
    ),
    (
        "BR-14",
        "run_id single authority + named interpreter + bash fallback + run_token reuse",
        "test_br14_the_skill_body_names_the_daemon_venv_interpreter",
    ),
    (
        "BR-15",
        "reportless-week detector (CITED) + lane-4 orphan listing via instance_read",
        "test_br15_detector_citation_nodes_exist_and_pass",
    ),
    (
        "PD-4a",
        "canonical per-lane result block frozen in decisions.md; sources CITE, never copy",
        "test_pd4a_canonical_block_is_frozen_in_decisions",
    ),
    (
        "P3 fence recipe",
        "disk-lane fence recipe anchored to the 2026-11-18 baseline artifact",
        "test_disk_lane_body_carries_the_four_fences",
    ),
    (
        "P3 no-write",
        "lane 4 only WATCHES — never writes maintenance_runs",
        "test_lane4_never_writes_maintenance_runs",
    ),
    (
        "BR-16b",
        "INDEX appends via write_file(append=True), chronological, dedup by run_id",
        "test_writer_skill_body_pins_the_index_append_mechanics",
    ),
    (
        "BR-18",
        "live_views root registration: name, mechanism, relative form, allowlist",
        "test_br18_element_2_root_name_is_maintenance_reports",
    ),
    (
        "P4 receipt",
        "receipt fields match the phase4 receipt shape",
        "test_receipt_cross_ref_matches_the_phase4_receipt_shape",
    ),
    (
        "P1 AC6",
        "failure-mode rows carry a named mitigation + a P5 anchor",
        "test_p1_failure_mode_rows_each_carry_a_named_mitigation",
    ),
    (
        "P2 skill versions",
        "6 lane skills + manifest/frontmatter version consistency (CITED)",
        "test_skill_version_consistency_citation_nodes_exist_and_pass",
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_meta() -> dict:
    """Load ``agents/maintenancer/meta.json`` as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _skill_body(name: str) -> str:
    """Return the body of ``skills-template/<name>.md`` (located by glob)."""
    matches = sorted(SKILLS_DIR.glob(f"{name}.md"))
    assert len(matches) == 1, (
        f"expected exactly one skills-template/{name}.md; got {matches}"
    )
    return _read(matches[0])


def _fenced_blocks(text: str) -> list[str]:
    """Return the bodies of every ```-fenced block in ``text``."""
    return re.findall(r"```[a-zA-Z0-9_-]*\n(.*?)```", text, re.DOTALL)


@lru_cache(maxsize=1)
def _tool_category_map() -> dict[str, frozenset[str]]:
    """Derive the REAL category -> tool-name map from the daemon source.

    The daemon stamps a category onto a tool via
    ``@register_tool_category("<cat>")`` (``daemon/tools/_tool_registry.py``);
    the tool name is the decorated function's name.  This helper mirrors
    that derivation by AST-scanning every module listed in the daemon's own
    ``CATEGORY_MODULES`` map — so the map can never drift from the source
    the way a hand-written mirror would.

    Deriving the map from source (rather than hard-coding it) is what gives
    the BR-10 exclusion pin TEETH: granting the whole ``"instance"`` or
    ``"council"`` category would leak the excluded names and redden the pin.
    """
    from daemon.tools._tool_registry import CATEGORY_MODULES

    tools_root = PROJECT_ROOT / "daemon" / "tools"
    categories: dict[str, set[str]] = {}
    for module_paths in CATEGORY_MODULES.values():
        paths = module_paths if isinstance(module_paths, list) else [module_paths]
        for module_path in paths:
            parts = module_path.split(".")
            file_path = tools_root
            for part in parts[2:]:  # skip the "daemon" / "tools" prefix
                file_path = file_path / part
            file_path = file_path.with_suffix(".py")
            if not file_path.exists():
                continue
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                category: str | None = None
                has_tool_decorator = False
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call):
                        func = dec.func
                        dec_name = (
                            func.attr
                            if isinstance(func, ast.Attribute)
                            else func.id if isinstance(func, ast.Name) else ""
                        )
                    elif isinstance(dec, ast.Attribute):
                        dec_name = dec.attr
                    elif isinstance(dec, ast.Name):
                        dec_name = dec.id
                    else:
                        continue
                    if (
                        dec_name == "register_tool_category"
                        and dec.args
                        and isinstance(dec.args[0], ast.Constant)
                    ):
                        category = str(dec.args[0].value)
                    elif dec_name == "tool":
                        has_tool_decorator = True
                if category and has_tool_decorator:
                    categories.setdefault(category, set()).add(node.name)
    return {key: frozenset(value) for key, value in categories.items()}


def _resolved_surface() -> set[str]:
    """Resolve the seat's granted tool surface via the daemon's own filter."""
    from daemon.tools.instance import resolve_tool_filter

    meta = _load_meta()
    categories = {key: sorted(value) for key, value in _tool_category_map().items()}
    resolved = resolve_tool_filter(
        allow=list(meta["tools"]["allow"]),
        deny=list(meta["tools"]["deny"]),
        tool_categories=categories,
    )
    assert resolved is not None, (
        "resolve_tool_filter returned None (all tools allowed) — the "
        "seat's allow/deny grammar is no longer restrictive"
    )
    return set(resolved)


def _test_functions(path: Path) -> set[str]:
    """Return every test-function name defined in ``path`` (AST, no import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _module_literal(path: Path, name: str) -> object | None:
    """Return the module-level literal value of ``name`` in ``path``.

    AST-only (no import) — the daemon modules are never imported here, so
    this helper cannot boot or touch a database.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign):
            if node.targets and isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0], node.value
        if target is None or value is None:
            continue
        if not isinstance(target, ast.Name) or target.id != name:
            continue
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    return None


# ---------------------------------------------------------------------------
# Row 1 — BR-10 / P2-BR-8: the seat grant.
# ---------------------------------------------------------------------------
class TestBr10SeatGrant:
    """BR-8 grants FOUR LITERAL tool names; BR-10 pins the resolved surface.

    BR-8's whole point is that the seat does NOT receive the whole
    ``"instance"`` category — the seat already holds ``instance_read`` +
    ``instance_ops``, so the four spawn/message literals are enough.
    """

    def test_br10_allow_contains_the_four_literal_tool_names(self) -> None:
        """The allow list carries the FOUR LITERAL names (BR-8)."""
        allow = _load_meta()["tools"]["allow"]
        for tool_name in THE_FOUR_LITERAL_TOOLS:
            assert tool_name in allow, (
                f"meta.json tools.allow must carry the LITERAL tool name "
                f"{tool_name!r} (BR-8 — the seat spawns and messages its "
                f"own lane children). allow={allow}"
            )

    def test_br10_allow_does_not_grant_the_bare_instance_category(self) -> None:
        """The whole ``"instance"`` category is NOT granted (BR-8)."""
        allow = _load_meta()["tools"]["allow"]
        assert "instance" not in allow, (
            "'instance' (the whole category) must NOT be in tools.allow — "
            f"BR-8 grants four LITERAL names only. allow={allow}"
        )

    def test_br10_deny_is_byte_equality_with_the_three_names(self) -> None:
        """The deny list is EXACTLY {git_commit, edit_file, write_file}."""
        deny = _load_meta()["tools"]["deny"]
        assert set(deny) == set(EXPECTED_DENY), (
            f"meta.json tools.deny must be exactly {sorted(EXPECTED_DENY)}; "
            f"got {sorted(deny)}"
        )
        assert len(deny) == len(EXPECTED_DENY), (
            f"tools.deny carries a duplicate or an extra entry: {deny}"
        )

    def test_br10_resolved_surface_excludes_the_omitted_names(self) -> None:
        """The RESOLVED surface omits terminate/pause/resume/tunable/council."""
        resolved = _resolved_surface()
        for tool_name in sorted(BR10_EXCLUDED_NAMES):
            assert tool_name not in resolved, (
                f"{tool_name!r} must NOT be in the seat's resolved tool "
                f"surface (BR-10). resolved={sorted(resolved)}"
            )
        for prefix in BR10_EXCLUDED_PREFIXES:
            leaked = sorted(n for n in resolved if n.startswith(prefix))
            assert not leaked, (
                f"the {prefix!r} family must NOT be in the seat's resolved "
                f"tool surface (BR-10). leaked={leaked}"
            )

    def test_br10_resolved_surface_contains_the_four_literal_names(self) -> None:
        """The four granted literals DO survive the resolution."""
        resolved = _resolved_surface()
        for tool_name in THE_FOUR_LITERAL_TOOLS:
            assert tool_name in resolved, (
                f"{tool_name!r} must be in the seat's resolved tool surface "
                f"(BR-8 grant). resolved={sorted(resolved)}"
            )


# ---------------------------------------------------------------------------
# Row 2 — P3 lane 2 (POK): the prohibition strings.
# ---------------------------------------------------------------------------
class TestP3Lane2PokProhibition:
    """The POK lane is read-only: no author, no curate."""

    def test_pok_write_verbs_are_absent_and_the_query_verb_is_present(self) -> None:
        """``pok_upsert`` / ``pok_curate`` ABSENT; ``pok_query`` present."""
        body = _skill_body("lane-pok-sweep")
        for forbidden in ("pok_upsert", "pok_curate"):
            assert forbidden not in body, (
                f"the POK lane body must NOT name {forbidden!r} — the "
                f"lane-2 fence is prompt/workflow-level (the category "
                f"carries a write verb; the prohibition is the control)"
            )
        assert "pok_query" in body, (
            "the POK lane body must name pok_query (the read/query verb "
            "is the lane's only POK tool)"
        )

    def test_pok_lane_states_the_query_verb_is_its_only_pok_verb(self) -> None:
        """The body states the read-only fence in prose (enforcement exists)."""
        body = _skill_body("lane-pok-sweep")
        assert "only POK verb" in body, (
            "the lane-pok-sweep body must state that the query verb is its "
            "ONLY POK verb (the prohibition must be encoded, not implied)"
        )


# ---------------------------------------------------------------------------
# Row 3 — BR-9: run containment (rule.md + workflow.md mirror).
# ---------------------------------------------------------------------------
RULE_CONTAINMENT_CLAUSES: tuple[str, ...] = (
    "touches ONLY instances I spawned in THIS run",
    "never message, pause, resume, or terminate an instance not spawned this run",
    "audited `instance_terminate` act",
    "never pass `join_room` or `member_name`",
)

WORKFLOW_CONTAINMENT_CLAUSES: tuple[str, ...] = (
    "Touch ONLY children of THIS run",
    "Never message, pause, resume, or terminate an instance not spawned this run",
    "audited `instance_terminate` act",
    "Never pass `join_room` or `member_name` at spawn",
)


class TestBr9RunContainment:
    """BR-9 — the Cardinal-tier run-containment rule and its workflow mirror."""

    @pytest.mark.parametrize(
        "clause",
        RULE_CONTAINMENT_CLAUSES,
        ids=lambda c: f"rule::{c[:44]}",
    )
    def test_rule_md_encodes_the_run_containment_clauses(self, clause: str) -> None:
        """Each containment clause must appear in rule.md (Cardinal tier)."""
        text = _read(RULE_PATH)
        assert clause in text, (
            f"rule.md must encode the BR-9 containment clause {clause!r}"
        )

    @pytest.mark.parametrize(
        "clause",
        WORKFLOW_CONTAINMENT_CLAUSES,
        ids=lambda c: f"workflow::{c[:44]}",
    )
    def test_workflow_md_mirrors_the_run_containment_clauses(self, clause: str) -> None:
        """Each containment clause must appear in workflow.md (the mirror)."""
        text = _read(WORKFLOW_PATH)
        assert clause in text, (
            f"workflow.md must mirror the BR-9 containment clause {clause!r}"
        )


# ---------------------------------------------------------------------------
# Row 4 — BR-12: the lane-status mapping table.
# ---------------------------------------------------------------------------
def _lane_status_table() -> dict[str, tuple[str, str]]:
    """Parse the BR-12 lane-status mapping table out of workflow.md.

    The status cells are backtick-quoted in the prompt (`` `failed` ``);
    they are normalized (backticks stripped) before comparison.
    """
    text = _read(WORKFLOW_PATH)
    match = re.search(
        r"###\s*Lane status mapping \(BR-12\)(?P<body>.*?)(?=\n###\s|\Z)",
        text,
        re.DOTALL,
    )
    assert match, "workflow.md must carry a 'Lane status mapping (BR-12)' section"
    rows: dict[str, tuple[str, str]] = {}
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3:
            continue
        if cells[0] in ("Child outcome",) or set(cells[0]) <= {"-", " "}:
            continue
        rows[cells[0]] = (cells[1].replace("`", ""), cells[2].replace("`", ""))
    assert rows, (
        "the BR-12 lane-status mapping table parsed to zero rows — the "
        "table shape drifted"
    )
    return rows


class TestBr12LaneStatusMapping:
    """BR-12 — the lane-status mapping table (P2 T2.4 authoritative)."""

    def test_br12_never_dispatched_maps_to_failed_with_incomplete(self) -> None:
        """A lane never dispatched before the global deadline => failed + [incomplete]."""
        rows = _lane_status_table()
        outcome = next(
            (key for key in rows if key.lower().startswith("never dispatched")),
            None,
        )
        assert outcome is not None, (
            "the BR-12 table must carry a 'never dispatched before the global "
            f"deadline' row; rows={sorted(rows)}"
        )
        status, note = rows[outcome]
        assert status == "failed", (
            f"the never-dispatched row must map to 'failed'; got {status!r}"
        )
        assert "[incomplete]" in note, (
            f"the never-dispatched row must append '[incomplete]'; got {note!r}"
        )

    def test_br12_error_maps_to_failed(self) -> None:
        """A child ERROR maps to ``failed``."""
        rows = _lane_status_table()
        outcome = next((key for key in rows if key.strip() == "`ERROR`"), None)
        assert outcome is not None, (
            f"the BR-12 table must carry an 'ERROR' row; rows={sorted(rows)}"
        )
        assert rows[outcome][0] == "failed", (
            f"the ERROR row must map to 'failed'; got {rows[outcome][0]!r}"
        )

    def test_br12_completed_maps_to_ok_or_degraded(self) -> None:
        """A COMPLETED child maps to ``ok`` or ``degraded`` per its result block."""
        rows = _lane_status_table()
        outcome = next(
            (key for key in rows if key.startswith("`COMPLETED`")), None
        )
        assert outcome is not None, (
            f"the BR-12 table must carry a 'COMPLETED' row; rows={sorted(rows)}"
        )
        assert rows[outcome][0] in ("ok or degraded", "ok|degraded"), (
            f"the COMPLETED row must map to 'ok' or 'degraded'; got "
            f"{rows[outcome][0]!r}"
        )

    def test_br12_silent_past_deadline_is_degraded_plus_incomplete(self) -> None:
        """Silence past the deadline degrades the lane and marks it incomplete."""
        rows = _lane_status_table()
        outcome = next(
            (key for key in rows if key.lower().startswith("silent past deadline")),
            None,
        )
        assert outcome is not None, (
            f"the BR-12 table must carry a 'silent past deadline' row; "
            f"rows={sorted(rows)}"
        )
        status, note = rows[outcome]
        assert status == "degraded", (
            f"the silent-past-deadline row must map to 'degraded'; got {status!r}"
        )
        assert "[incomplete]" in note, (
            f"the silent-past-deadline row must append '[incomplete]'; got {note!r}"
        )


# ---------------------------------------------------------------------------
# Row 6 — BR-13: the pre-decided writer ladder.
# ---------------------------------------------------------------------------
class TestBr13WriterLadder:
    """BR-13 — L0 -> L3, pre-decided, with the DP-1 ruling row verbatim."""

    def test_workflow_encodes_the_l0_to_l3_writer_ladder(self) -> None:
        """workflow.md carries the four pre-decided rungs."""
        text = _read(WORKFLOW_PATH)
        assert "### Report-writer ladder (BR-13)" in text, (
            "workflow.md must carry the 'Report-writer ladder (BR-13)' section"
        )
        rungs = {
            "L0": "**L0**",
            "L1": "**L1**",
            "L2": "**L2**",
            "L3": "**L3**",
        }
        for rung, marker in rungs.items():
            assert marker in text, (
                f"the BR-13 ladder must encode rung {rung} (marker {marker!r})"
            )

    def test_br13_l0_write_order_is_run_then_receipt_then_index_last(self) -> None:
        """L0's write order is run -> receipt -> INDEX last (both bodies)."""
        workflow = _read(WORKFLOW_PATH)
        writer = _skill_body(WRITER_SKILL)
        for text, label in ((workflow, "workflow.md"), (writer, WRITER_SKILL)):
            assert "run → receipt → INDEX last" in text, (
                f"{label} must state the L0 write order "
                f"'run → receipt → INDEX last' (BR-13 — a crash leaves no "
                f"half-ledger row)"
            )

    def test_br13_l1_is_one_fresh_minimal_stub_writer(self) -> None:
        """L1 = ONE fresh minimal-stub writer, no skill load."""
        text = _read(WORKFLOW_PATH)
        assert "ONE fresh minimal-stub writer, no skill load" in text, (
            "the BR-13 L1 rung must be ONE fresh minimal-stub writer with no "
            "skill load"
        )

    def test_br13_l2_is_one_bare_worker_child_all_failed(self) -> None:
        """L2 = ONE bare worker child, no load_skill, skeleton all-failed."""
        text = _read(WORKFLOW_PATH)
        assert "ONE bare worker child, no `load_skill`, skeleton all-failed" in text, (
            "the BR-13 L2 rung must be ONE bare worker child (no load_skill) "
            "with the skeleton all-failed shape"
        )

    def test_br13_l3_is_the_accepted_surfaced_reportless_terminal(self) -> None:
        """L3 = the accepted, surfaced reportless terminal (BR-15 surfaces it)."""
        text = _read(WORKFLOW_PATH)
        assert "accepted documented reportless terminal" in text, (
            "the BR-13 L3 rung must be the ACCEPTED documented reportless "
            "terminal (per DP-1)"
        )
        assert "reportless-week detector (BR-15)" in text, (
            "the BR-13 L3 rung must name the BR-15 reportless-week detector "
            "as the surfacing mechanism"
        )

    def test_br13_dp1_ruling_row_is_present_verbatim(self) -> None:
        """The DP-1 ruling row is quoted verbatim (P2 T2.4 requirement)."""
        text = _read(WORKFLOW_PATH)
        ruling = (
            "BR-13 L3 accepts a surfaced, documented reportless week when "
            "both writer rungs fail."
        )
        assert ruling in text, (
            "the DP-1 ruling row must be quoted verbatim in workflow.md: "
            f"{ruling!r}"
        )

    def test_br13_the_writer_skill_body_is_the_l0_rung(self) -> None:
        """The writer skill body states it is the L0 rung (ladder stated once)."""
        writer = _skill_body(WRITER_SKILL)
        assert "**L0**" in writer, (
            "the run-report-writer body must state it is the L0 rung"
        )
        assert "never restate the rungs" in writer, (
            "the writer body must state the ladder is stated ONCE (the "
            "orchestrator's workflow.md owns the rung list)"
        )


# ---------------------------------------------------------------------------
# Row 8 — BR-14: run_id authority, interpreter, bash fallback, run_token.
# ---------------------------------------------------------------------------
class TestBr14RunIdAuthority:
    """BR-14 — the report-writer child is the single run_id authority."""

    def test_br14_the_skill_body_names_the_daemon_venv_interpreter(self) -> None:
        """The writer body names the daemon venv interpreter (BR-14 PRIMARY)."""
        writer = _skill_body(WRITER_SKILL)
        assert DAEMON_VENV_PYTHON in writer, (
            f"the run-report-writer body must name the daemon venv interpreter "
            f"{DAEMON_VENV_PYTHON!r} (BR-14 PRIMARY rung)"
        )
        assert "bare `python3` fails" in writer, (
            "the run-report-writer body must state that bare python3 fails on "
            "this host (the reason the interpreter is NAMED)"
        )

    def test_br14_the_orchestrator_does_not_mint_the_run_id(self) -> None:
        """The seat does not mint (single authority = the writer child)."""
        workflow = _read(WORKFLOW_PATH)
        assert "I do not mint a run_id" in workflow, (
            "workflow.md must state the seat does NOT mint the run_id "
            "(BR-14 — the seat has no bash/filesystem)"
        )

    def test_br14_the_bash_fallback_is_a_binding_rung(self) -> None:
        """The bash format-spec fallback is a BINDING rung in the skill body."""
        writer = _skill_body(WRITER_SKILL)
        fallback = "ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)"
        assert fallback in writer, (
            "the run-report-writer body must carry the bash fallback "
            f"{fallback!r} as a BINDING rung (BR-14)"
        )
        assert "FALLBACK (BINDING rung)" in writer, (
            "the bash fallback must be labelled a BINDING rung (not an "
            "advisory aside)"
        )

    def test_br14_the_run_id_mint_uses_the_named_interpreter(self) -> None:
        """MINT via the NAMED interpreter and assert the canonical shape.

        Bare ``python3`` fails on this host, so the mint must run through
        the daemon venv interpreter the skill body names.
        """
        mint = (
            "from daemon.services.maintenance_run_identity import "
            "new_maintenance_run_id; print(new_maintenance_run_id())"
        )
        completed = subprocess.run(
            [DAEMON_VENV_PYTHON, "-c", mint],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, (
            f"the run_id mint via {DAEMON_VENV_PYTHON} failed "
            f"(rc={completed.returncode}): {completed.stderr[-800:]}"
        )
        minted = completed.stdout.strip()
        assert RUN_ID_PATTERN.match(minted), (
            f"the minted run_id must match {RUN_ID_PATTERN.pattern}; got {minted!r}"
        )

    def test_bare_python3_cannot_import_the_daemon_dependencies(self) -> None:
        """The documented reason the interpreter is NAMED (BR-14 leg detail).

        ``bare python3 fails on this host (ModuleNotFoundError: sqlalchemy)``
        — a test that minted with ``python3`` would false-GREEN/RED for the
        wrong reason.  The BARE interpreter is the one on PATH (NOT the
        venv interpreter this test itself runs under).
        """
        import shutil

        bare = shutil.which("python3")
        if bare is None:
            pytest.skip("no bare `python3` on PATH")
        completed = subprocess.run(
            [bare, "-c", "import sqlalchemy"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode == 0:
            pytest.skip(
                "the bare interpreter CAN import sqlalchemy — the documented "
                "bare-python3 failure does not reproduce here"
            )
        assert "sqlalchemy" in completed.stderr, (
            "the bare-interpreter failure must be the documented "
            f"sqlalchemy import error; got: {completed.stderr[-400:]}"
        )

    def test_br14_run_token_reuse_survives_a_re_mint(self) -> None:
        """A re-dispatch reuses the SAME identity, so INDEX dedup survives.

        The behavioural spine: the canonical helper mints a DIFFERENT id per
        call (so a re-mint would defeat dedup), while the run_token reuse
        rule keeps the identity stable across re-dispatches — the INDEX
        dedups on that stable identity.
        """
        mint = (
            "from daemon.services.maintenance_run_identity import "
            "new_maintenance_run_id; print(new_maintenance_run_id())"
        )
        first = subprocess.run(
            [DAEMON_VENV_PYTHON, "-c", mint],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        second = subprocess.run(
            [DAEMON_VENV_PYTHON, "-c", mint],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert first.returncode == 0 and second.returncode == 0, (
            "the run_id mint via the named interpreter failed"
        )
        minted_a = first.stdout.strip()
        minted_b = second.stdout.strip()
        assert minted_a != minted_b, (
            "two independent mints must differ — if they did not, the "
            "run_token-reuse rule would not be load-bearing"
        )

        # The INDEX dedup: rows carry the run_id, one row per run.
        index_rows = [minted_a]
        index_rows.append(minted_a)  # the re-dispatch reuses the SAME run_id
        deduped = list(dict.fromkeys(index_rows))
        assert deduped == [minted_a], (
            "the INDEX dedup by run_id must collapse the re-dispatch's "
            "duplicate row onto the original identity"
        )

        # The body binds the rule that makes the dedup hold.
        writer = _skill_body(WRITER_SKILL)
        assert "Re-dispatches reuse the incoming `run_token`" in writer, (
            "the run-report-writer body must state that re-dispatches reuse "
            "the incoming run_token (BR-14 — the seat cannot mint)"
        )
        assert "INDEX dedup survives re-mints" in writer, (
            "the run-report-writer body must state that INDEX dedup survives "
            "re-mints (BR-14)"
        )


# ---------------------------------------------------------------------------
# Row 9 — BR-15: the reportless-week detector (CITED) + the lane-4 orphan
# listing.
# ---------------------------------------------------------------------------
BR15_CITATION_NODES: tuple[str, ...] = (
    "tests/unit/test_maintenance_seed_guard.py::TestBr15ReportlessWeek::test_no_execution_record_means_nothing_to_detect",
    "tests/unit/test_maintenance_seed_guard.py::TestBr15ReportlessWeek::test_an_empty_root_is_not_a_reportless_week",
    "tests/unit/test_maintenance_seed_guard.py::TestBr15ReportlessWeek::test_a_fire_with_a_report_is_not_a_miss",
)

LANE4_SKILL = "lane-abnormality-check"


class TestBr15ReportlessWeekAndOrphanListing:
    """BR-15 — the detector half is CITED (P1 landed it); the orphan-listing
    half is pinned here."""

    def test_br15_detector_citation_nodes_exist_and_pass(self) -> None:
        """The cited BR-15 detector nodes exist in collection AND pass."""
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *BR15_CITATION_NODES,
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, (
            "the CITED BR-15 reportless-week detector nodes must exist in "
            "collection and pass; rc="
            f"{completed.returncode}\n{completed.stdout[-2000:]}"
        )

    def test_br15_the_lane4_orphan_listing_rides_the_existing_instance_read_surface(
        self,
    ) -> None:
        """Lane 4 lists NON-TERMINAL maintenancer-parented instances via
        the EXISTING ``instance_read`` surface."""
        body = _skill_body(LANE4_SKILL)
        assert "NON-TERMINAL instances whose parent is a maintenancer instance" in body, (
            "the lane-4 body must specify the orphan listing: NON-TERMINAL "
            "instances whose parent is a maintenancer instance (BR-15)"
        )
        assert "instance_read" in body, (
            "the lane-4 orphan listing must ride the EXISTING instance_read "
            "surface (no new grant)"
        )

    def test_br15_the_orphan_listing_never_terminates(self) -> None:
        """The lane LISTS orphans; it never terminates them."""
        body = _skill_body(LANE4_SKILL)
        assert "I list them; I never terminate them" in body, (
            "the lane-4 body must state it lists the orphans and never "
            "terminates them (BR-15 — the audited kill is the owning lane's)"
        )

    def test_br15_the_seat_grant_carries_no_new_orphan_listing_grant(self) -> None:
        """No new grant was added for the orphan listing."""
        allow = _load_meta()["tools"]["allow"]
        assert set(allow) == set(EXPECTED_ALLOW_SET), (
            "the seat's grant set changed — BR-15's lane-4 orphan listing "
            "must ride the EXISTING instance_read surface (no new grant). "
            f"added={sorted(set(allow) - set(EXPECTED_ALLOW_SET))} "
            f"removed={sorted(set(EXPECTED_ALLOW_SET) - set(allow))}"
        )

    def test_br15_the_detector_never_self_heals_or_refires(self) -> None:
        """The lane-4 body forbids self-heal / re-fire of a reportless week."""
        body = _skill_body(LANE4_SKILL)
        assert "never one I self-heal" in body, (
            "the lane-4 body must state the reportless-week residual is "
            "never self-healed (BR-15 — surface only)"
        )
        assert "never one I re-fire" in body, (
            "the lane-4 body must state the reportless-week residual is "
            "never re-fired (BR-15 — surface only)"
        )


# ---------------------------------------------------------------------------
# Row 10 — PD-4a: the frozen canonical per-lane result block.
# ---------------------------------------------------------------------------
def _pd4a_block() -> str:
    """Return the fenced PD-4a canonical block from decisions.md."""
    text = _read(DECISIONS_PATH)
    marker = "PD-4a — CANONICAL PER-LANE RESULT BLOCK"
    assert marker in text, (
        f"decisions.md must carry the '{marker}' heading (the frozen "
        f"canonical schema authority)"
    )
    index = text.index(marker)
    blocks = _fenced_blocks(text[index:])
    assert blocks, "the PD-4a heading must be followed by its fenced canonical form"
    return blocks[0]


def _pd4a_fields(block: str) -> dict[str, str]:
    """Parse the top-level field names out of the PD-4a canonical block."""
    fields: dict[str, str] = {}
    for line in block.splitlines():
        match = re.match(r"^([a-z][a-z_]*):(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


class TestPd4aCanonicalSchema:
    """PD-4a — ONE schema authority; every source CITES it, never copies it."""

    def test_pd4a_canonical_block_is_frozen_in_decisions(self) -> None:
        """The canonical block carries the full superset field set."""
        fields = _pd4a_fields(_pd4a_block())
        assert set(fields) == set(PD4A_EXPECTED_FIELDS), (
            "the PD-4a canonical field set drifted. expected="
            f"{sorted(PD4A_EXPECTED_FIELDS)} got={sorted(fields)}"
        )
        for mandatory in ("measured_at", "actions_taken"):
            assert mandatory in fields, (
                f"PD-4a must carry {mandatory!r} — silently dropping it "
                f"from any lane's receipt entry is a schema violation"
            )

    def test_pd4a_declares_the_three_recommendation_classes(self) -> None:
        """The three recommendation classes are inside the canonical block."""
        block = _pd4a_block()
        for class_name in PD4A_RECOMMENDATION_CLASSES:
            assert class_name in block, (
                f"the PD-4a block must declare the {class_name!r} "
                f"recommendation class"
            )

    def test_pd4a_status_and_lane_vocabularies_are_closed(self) -> None:
        """The lane vocabulary names all five lanes; the status set is closed."""
        block = _pd4a_block()
        fields = _pd4a_fields(block)
        for lane in LANE_SKILLS:
            assert lane in fields["lane"], (
                f"the PD-4a lane vocabulary must name {lane!r}"
            )
        assert "ok | degraded | failed" in fields["status"], (
            f"the PD-4a status vocabulary drifted: {fields['status']!r}"
        )

    @pytest.mark.parametrize(
        "source",
        (*LANE_SKILLS, WRITER_SKILL),
        ids=lambda s: s,
    )
    def test_pd4a_sources_cite_the_schema_and_never_copy_it(self, source: str) -> None:
        """Each per-lane source CITES PD-4a and embeds NO divergent copy."""
        body = _skill_body(source)
        assert "PD-4a" in body, (
            f"{source} must CITE the canonical PD-4a schema by reference"
        )
        for block in _fenced_blocks(body):
            for field in ("measured_at:", "actions_taken:", "evidence_paths:"):
                assert field not in block, (
                    f"{source} embeds a divergent COPY of the PD-4a schema "
                    f"(a fenced block defines {field!r}) — the canonical "
                    f"schema authority is decisions.md PD-4a"
                )

    def test_pd4a_the_receipt_template_cites_rather_than_re_embeds(self) -> None:
        """phase4's receipt template cites the block; it does not re-embed it."""
        text = _read(PHASE4_PATH)
        blocks = _fenced_blocks(text)
        receipt_blocks = [
            block
            for block in blocks
            if "run_id:" in block and "trigger_type:" in block and "lanes:" in block
        ]
        assert receipt_blocks, (
            "phase4-plan.md must carry the receipt header field block"
        )
        receipt = receipt_blocks[0]
        assert "PD-4a canonical block" in receipt, (
            "the receipt template must cite the PD-4a canonical block by "
            "reference (never a divergent embedded copy)"
        )
        for field in ("measured_at:", "actions_taken:", "evidence_paths:"):
            assert field not in receipt, (
                f"the receipt template re-embeds the lane schema ({field!r}) "
                f"— it must cite PD-4a by reference"
            )


# ---------------------------------------------------------------------------
# Row 11 — P3: the disk-lane fence recipe, anchored to the baseline.
# ---------------------------------------------------------------------------
class TestP3DiskLaneFenceRecipe:
    """P3 — the lane-1 fence recipe and its baseline anchor."""

    def test_disk_lane_body_carries_the_four_fences(self) -> None:
        """baseline/recency, protected set, open-by-process, bytes+inodes."""
        body = _skill_body("lane-disk-sweep")
        fences = {
            "recency subtree scan": "Recency subtree scan",
            "open-by-process sweep": "Open-by-process sweep",
            "evidence-pointer protect-list": "Evidence-pointer protect-list",
            "worktree disposition": "Worktree disposition",
        }
        for label, marker in fences.items():
            assert marker in body, (
                f"the lane-disk-sweep body must carry the {label!r} fence"
            )
        assert "bytes AND inodes" in body, (
            "the lane-disk-sweep body must require bytes AND inodes "
            "(the metrics contract)"
        )

    def test_disk_lane_body_carries_the_protected_set(self) -> None:
        """The protected set is named and is never touched."""
        body = _skill_body("lane-disk-sweep")
        assert "NEVER touched — the protected set" in body, (
            "the lane-disk-sweep body must declare the protected set as never "
            "touched"
        )

    def test_disk_lane_fence_recipe_is_anchored_to_the_baseline_artifact(self) -> None:
        """The fence recipe's baseline anchor resolves to a REAL artifact.

        The baseline artifact is named by P3 (phase3-plan.md) at the exact
        path; the anchor must resolve, and the artifact must carry the same
        fence vocabulary the lane body implements.
        """
        assert DISK_BASELINE_PATH.exists(), (
            f"the disk-lane baseline anchor must resolve to a real artifact "
            f"at {DISK_BASELINE_PATH}"
        )
        phase3 = _read(PHASE3_PATH)
        assert str(
            DISK_BASELINE_PATH.relative_to(PROJECT_ROOT)
        ) in phase3, (
            "phase3-plan.md must name the disk-lane baseline anchor at the "
            "exact path the pin resolves"
        )
        baseline = _read(DISK_BASELINE_PATH)
        for vocabulary in ("Open-by-process", "protected", "inodes", "bytes"):
            assert vocabulary.lower() in baseline.lower(), (
                f"the baseline artifact must carry the {vocabulary!r} fence "
                f"vocabulary the lane recipe anchors to"
            )


# ---------------------------------------------------------------------------
# Row 12 — P3: lane 4 only WATCHES.
# ---------------------------------------------------------------------------
class TestP3Lane4NoWrite:
    """P3 — the old tier is separate and live; lane 4 only WATCHES it."""

    def test_lane4_never_writes_maintenance_runs(self) -> None:
        """``maintenance_runs`` is SELECT-only for lane 4 (D-C)."""
        body = _skill_body(LANE4_SKILL)
        assert "`maintenance_runs` is SELECT-only for me (D-C)" in body, (
            "the lane-4 body must state that maintenance_runs is "
            "SELECT-only for it (D-C — no DB writes into the old tier's ledger)"
        )
        assert "never write to it" in body, (
            "the lane-4 body must state it never writes to the old tier"
        )

    def test_lane4_never_authors_anomaly_records(self) -> None:
        """Lane 4 classifies; it never authors anomaly records."""
        body = _skill_body(LANE4_SKILL)
        assert "never mutate state or author anomaly records" in body, (
            "the lane-4 body must state it never authors anomaly records"
        )


# ---------------------------------------------------------------------------
# Row 13 — BR-16b: the INDEX mechanics.
# ---------------------------------------------------------------------------
class TestBr16bIndexMechanics:
    """BR-16b — INDEX appends via ``write_file(append=True)``, dedups by run_id."""

    def test_writer_skill_body_pins_the_index_append_mechanics(self) -> None:
        """append=True + run_id per row + chronological + dedup."""
        writer = _skill_body(WRITER_SKILL)
        assert "write_file(append=True)" in writer, (
            "the run-report-writer body must pin the INDEX append mechanism "
            "as write_file(append=True) (BR-16b)"
        )
        assert "with the run_id on each row" in writer, (
            "the run-report-writer body must pin that every INDEX row carries "
            "the run_id (BR-16b dedup key)"
        )
        assert "INDEX.md" in writer, (
            "the run-report-writer body must name the INDEX.md artifact"
        )

    def test_writer_skill_body_forbids_shell_redirection_for_the_index(self) -> None:
        """No ``bash >>`` / ``tee -a`` against the INDEX (BR-16b)."""
        writer = _skill_body(WRITER_SKILL)
        for forbidden in (">>", "tee -a"):
            assert forbidden not in writer, (
                f"the run-report-writer body must not use {forbidden!r} "
                f"against the INDEX (BR-16b — appends go through "
                f"write_file(append=True))"
            )
        assert "never shell redirection" in writer, (
            "the run-report-writer body must state the INDEX is never "
            "written via shell redirection"
        )

    def test_index_rows_are_chronological_by_the_lexicographic_run_id(self) -> None:
        """The run_id prefix sorts chronologically (the INDEX ordering key)."""
        ids = [
            "ckpt-20261118_010000000000-aaaaaaaa",
            "ckpt-20261125_010000000000-bbbbbbbb",
            "ckpt-20261202_010000000000-cccccccc",
        ]
        assert sorted(ids) == ids, (
            "the canonical run_id prefix must sort chronologically "
            "(lexicographic = chronological, the INDEX ordering invariant)"
        )


# ---------------------------------------------------------------------------
# Row 14 — P4: the receipt schema cross-ref.
# ---------------------------------------------------------------------------
class TestP4ReceiptCrossRef:
    """P4 — the writer's receipt header matches phase4's receipt shape.

    Light cross-ref only — the ``trigger_type`` TOKEN ROUND-TRIP is T5.6's
    job (it already carries the round-trip); this pin does not duplicate it.
    """

    def test_receipt_cross_ref_matches_the_phase4_receipt_shape(self) -> None:
        """Every phase4 receipt header field is named in the writer body."""
        phase4 = _read(PHASE4_PATH)
        blocks = _fenced_blocks(phase4)
        receipt_blocks = [
            block
            for block in blocks
            if "run_id:" in block and "trigger_type:" in block and "lanes:" in block
        ]
        assert receipt_blocks, "phase4-plan.md must carry the receipt field block"
        receipt = receipt_blocks[0]
        header_fields = [
            line.split(":", 1)[0].strip()
            for line in receipt.splitlines()
            if line.startswith("  ") and re.match(r"^  [a-z_]+:", line)
        ]
        assert header_fields, "the receipt block must list its header fields"
        writer = _skill_body(WRITER_SKILL)
        for field in header_fields:
            assert field in writer, (
                f"the run-report-writer body must name the receipt field "
                f"{field!r} (P4 receipt shape cross-ref)"
            )

    def test_receipt_trigger_type_is_the_closed_scheduled_manual_set(self) -> None:
        """The receipt's trigger_type is the closed {scheduled, manual} set."""
        phase4 = _read(PHASE4_PATH)
        blocks = _fenced_blocks(phase4)
        receipt = next(
            block
            for block in blocks
            if "run_id:" in block and "trigger_type:" in block and "lanes:" in block
        )
        assert "trigger_type: scheduled | manual" in receipt, (
            "the phase4 receipt block must pin trigger_type to the closed "
            "scheduled|manual set"
        )
        writer = _skill_body(WRITER_SKILL)
        assert "embed `trigger_type` verbatim" in writer, (
            "the run-report-writer body must embed trigger_type verbatim "
            "(from the envelope — it never derives it and never reads the DB)"
        )


# ---------------------------------------------------------------------------
# Row 15 — P1 AC6: the failure-mode ledger.
# ---------------------------------------------------------------------------
def _failure_mode_rows() -> list[tuple[str, str, str]]:
    """Parse the phase1 failure-mode table into (failure, behavior, mitigation)."""
    text = _read(PHASE1_PATH)
    match = re.search(
        r"^##\s+Failure modes\s*$(?P<body>.*?)(?=^##\s|\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "phase1-plan.md must carry a '## Failure modes' section"
    rows: list[tuple[str, str, str]] = []
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3:
            continue
        if cells[0] == "Failure" or set(cells[0]) <= {"-", " "}:
            continue
        rows.append((cells[0], cells[1], cells[2]))
    assert len(rows) == 10, (
        f"the phase1 failure-mode table must carry 10 rows (AC6's 8/10 "
        f"denominator); parsed {len(rows)}: {[r[0] for r in rows]}"
    )
    return rows


#: The phase5 verification-pass rows that are NOT owned by this map — they
#: are owned by a SIBLING pin file (P4's own lane landed the route, the
#: live-view registration and the FE gear-menu pins).  This map POINTS at
#: them; it never duplicates their coverage.
SIBLING_OWNED_ROWS: tuple[tuple[str, str], ...] = (
    (
        "BR-16",
        "tests/unit/test_maintenance_results_root_equivalence.py::test_all_three_code_surfaces_identical",
    ),
    (
        "BR-17",
        "tests/unit/routers/test_maintenance_reports_router.py::test_canonical_run_id_matches_the_pattern",
    ),
    (
        "BR-19",
        "frontend/src/app/pages/report/report.pins.spec.ts",
    ),
)

API_PATH = PROJECT_ROOT / "daemon" / "api.py"
MAINTENANCE_REPORTS_ROUTER_PATH = (
    PROJECT_ROOT / "daemon" / "routers" / "maintenance_reports.py"
)

#: The BRs whose checks are owned by the SIBLING P5 tasks (T5.1-T5.4,
#: T5.6, T5.7) — out of this map's scope.
SIBLING_P5_TASK_OWNED_BRS: frozenset[str] = frozenset(
    {"BR-1", "BR-2", "BR-3", "BR-4", "BR-5", "BR-6", "BR-7", "BR-20"}
)


class TestBr18LiveViewRootRegistration:
    """BR-18 — the live_views root registration's FOUR pinned elements.

    The phase-5 verification-pass map assigns BR-18 to this task; the
    four elements are pinned from the daemon source (AST + call-site
    ordering) — no daemon boot, no DB.
    """

    def test_br18_element_2_root_name_is_maintenance_reports(self) -> None:
        """Element 2 — the root NAME is ``maintenance-reports``."""
        root_name = _module_literal(
            MAINTENANCE_REPORTS_ROUTER_PATH, "LIVE_VIEW_ROOT_NAME"
        )
        assert root_name == "maintenance-reports", (
            "the live-view root name must be 'maintenance-reports'; got "
            f"{root_name!r}"
        )

    def test_br18_element_4_allowlist_is_md_only_with_the_filename_patterns(
        self,
    ) -> None:
        """Element 4 — ``.md`` only, restricted to the three filename patterns."""
        extensions = _module_literal(
            MAINTENANCE_REPORTS_ROUTER_PATH, "LIVE_VIEW_ALLOWED_EXTENSIONS"
        )
        patterns = _module_literal(
            MAINTENANCE_REPORTS_ROUTER_PATH, "LIVE_VIEW_FILENAME_PATTERNS"
        )
        assert extensions == ("md",), (
            f"the live-view root must allow the .md extension only; got {extensions!r}"
        )
        assert patterns == ("run-*.md", "receipt-*.md", "INDEX.md"), (
            "the live-view FILENAME allowlist must be "
            f"('run-*.md', 'receipt-*.md', 'INDEX.md'); got {patterns!r}"
        )

    def test_br18_element_1_mechanism_is_the_daemon_injected_lifespan_call(
        self,
    ) -> None:
        """Element 1 — daemon-injected at LIFESPAN (never config-declared)."""
        source = API_PATH.read_text(encoding="utf-8")
        assert "def inject_maintenance_results_live_view_root(" in source, (
            "daemon/api.py must define inject_maintenance_results_live_view_root "
            "(BR-18 element 1 — the daemon-injected mechanism)"
        )
        tree = ast.parse(source)
        lifespan = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "lifespan"
            ),
            None,
        )
        assert lifespan is not None, (
            "daemon/api.py must define the lifespan function (BR-18 element 1)"
        )
        calls = [
            node
            for node in ast.walk(lifespan)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name)
                 and node.func.id == "inject_maintenance_results_live_view_root")
                or (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "inject_maintenance_results_live_view_root")
            )
        ]
        assert calls, (
            "the maintenance-results live-view root must be injected FROM the "
            "lifespan (BR-18 element 1) — a config-declared root would "
            "silently vanish on promote"
        )

    def test_br18_element_3_form_is_the_relative_root_resolved_at_startup(
        self,
    ) -> None:
        """Element 3 — the relative ``.agents/maintenancer/RESULTS/`` form."""
        source = API_PATH.read_text(encoding="utf-8")
        assert ".agents/maintenancer/RESULTS/" in source, (
            "the registration must name the RELATIVE artifact root "
            "(.agents/maintenancer/RESULTS/) — no absolute path in config"
        )
        assert "root_dir = RESULTS_ROOT" in source, (
            "the registration must reuse the router's already-resolved "
            "RESULTS_ROOT (ONE resolution moment, ONE root basis)"
        )
        assert "root_dir.is_absolute()" in source, (
            "a relative root must leave the entry ABSENT rather than "
            "publishing a path that can never resolve"
        )


#: The phase5 map assigns each phase1 BR to the P5 task that anchors it.
BR_TO_P5_TASK: dict[str, str] = {
    "BR-1": "T5.4",
    "BR-2": "T5.3",
    "BR-3": "T5.4",
    "BR-4": "T5.3",
    "BR-5": "T5.2",
    "BR-6": "T5.1",
}

#: The phase1 task that carries each phase1 BR (phase5-plan.md's
#: "T5.1 (BR-6), T5.2 (BR-5), T5.3 (BR-4, BR-2), T5.4 (BR-1, BR-3)").
TASK_TO_P5_TASK: dict[str, str] = {
    "Task 1.2": "T5.2",
    "Task 1.3": "T5.3",
    "Task 1.4": "T5.4",
    "Task 1.5": "T5.1",
}

#: Per-row named mitigation — the mechanism each failure-mode row names.
#: Keyed by a substring of the failure cell; the value is the named
#: mitigation that MUST appear in that row's mitigation cell.
NAMED_MITIGATIONS: tuple[tuple[str, str], ...] = (
    ("Run fails", "Task 1.3 seam"),
    ("Adapter dies", "Chain-aware skip"),
    ("Row archived by source-GC", "Archive-aware recreate"),
    ("Daemon down at fire", "BR-1 predicate"),
    ("Outage > 24h", "BR-2"),
    ("Intra-TTL claim race", "BR-2"),
    ("Multi-week outage", "BR-1 sub-semantics"),
    ("Operator pauses / cancels", "Respect"),
    ("Window knobs invalid", "Fail-closed"),
    ("Anchored instant outside", "Fail-closed"),
)


class TestP1Ac6FailureModeLedger:
    """P1 AC6 — every failure-mode row carries a named mitigation and a P5
    anchor (the ledger the P1 acceptance criterion names)."""

    def test_p1_failure_mode_rows_each_carry_a_named_mitigation(self) -> None:
        """Every row's mitigation cell names its mechanism (AC6)."""
        rows = _failure_mode_rows()
        for failure_substring, named_mitigation in NAMED_MITIGATIONS:
            matches = [
                row for row in rows if failure_substring in row[0]
            ]
            assert len(matches) == 1, (
                f"expected exactly one failure-mode row matching "
                f"{failure_substring!r}; got {[r[0] for r in matches]}"
            )
            failure, _behavior, mitigation = matches[0]
            assert mitigation, (
                f"the failure-mode row {failure!r} has an EMPTY mitigation "
                f"cell (AC6 — each row carries a named mitigation)"
            )
            assert named_mitigation in mitigation, (
                f"the failure-mode row {failure!r} must name its mitigation "
                f"{named_mitigation!r}; got {mitigation!r}"
            )

    def test_p1_failure_mode_rows_have_no_placeholder_mitigation(self) -> None:
        """No mitigation cell is a placeholder."""
        rows = _failure_mode_rows()
        for failure, _behavior, mitigation in rows:
            for placeholder in ("TBD", "TODO", "N/A", "FIXME", "..."):
                assert placeholder not in mitigation, (
                    f"the failure-mode row {failure!r} carries the "
                    f"placeholder {placeholder!r} instead of a mitigation"
                )

    def test_p1_every_failure_mode_anchor_resolves_to_an_existing_p5_task(
        self,
    ) -> None:
        """Each Task/BR/P5 reference in the table resolves to a REAL P5 task."""
        phase5 = _read(
            PROJECT_ROOT
            / ".agents"
            / "shared"
            / "planning"
            / "maintenance-system"
            / "phase5-plan.md"
        )
        rows = _failure_mode_rows()
        resolved: dict[str, set[str]] = {}
        for failure, _behavior, mitigation in rows:
            tasks: set[str] = set()
            if "P5 " in mitigation:
                tasks |= set(re.findall(r"P5 (T5\.\d+)", mitigation))
            for br, task in BR_TO_P5_TASK.items():
                if br in mitigation:
                    tasks.add(task)
            for task_ref, task in TASK_TO_P5_TASK.items():
                if task_ref in mitigation:
                    tasks.add(task)
            resolved[failure] = tasks
            for task in sorted(tasks):
                assert f"Task {task.removeprefix('T')}" in phase5, (
                    f"the failure-mode row {failure!r} anchors {task!r}, but "
                    f"phase5-plan.md carries no 'Task {task.removeprefix('T')}'"
                )

    def test_p1_runtime_testable_rows_carry_a_p5_anchor(self) -> None:
        """Rows with a runtime-testable outcome carry a P5 anchor (AC6).

        AC6's ledger states the floor as 8/10 ("the two documentation-only
        rows carry none by design").  This pin measures the BODY: a row
        counts as anchored when its mitigation names a P5 anchor directly,
        or names a phase1 Task / BR that the phase5 map assigns to a P5
        task.
        """
        phase5 = _read(
            PROJECT_ROOT
            / ".agents"
            / "shared"
            / "planning"
            / "maintenance-system"
            / "phase5-plan.md"
        )
        rows = _failure_mode_rows()
        anchored: list[str] = []
        for failure, _behavior, mitigation in rows:
            if "P5 " in mitigation:
                anchored.append(failure)
                continue
            for br, task in BR_TO_P5_TASK.items():
                if br in mitigation:
                    anchored.append(failure)
                    assert f"Task {task.removeprefix('T')}" in phase5, (
                        f"the BR->P5 anchor map names {br} -> {task}, but "
                        f"phase5-plan.md does not carry 'Task {task.removeprefix('T')}'"
                    )
                    break
            else:
                for task_ref, task in TASK_TO_P5_TASK.items():
                    if task_ref in mitigation:
                        anchored.append(failure)
                        assert f"Task {task.removeprefix('T')}" in phase5, (
                            f"the Task->P5 anchor map names {task_ref} -> "
                            f"{task}, but phase5-plan.md does not carry "
                            f"'Task {task.removeprefix('T')}'"
                        )
                        break
        assert len(anchored) >= 8, (
            f"AC6 requires the runtime-testable failure-mode rows to carry a "
            f"P5 anchor (the ledger's floor is 8/10); only {len(anchored)} of "
            f"{len(rows)} rows carry one. unanchored="
            f"{[r[0] for r in rows if r[0] not in anchored]}"
        )

    def test_p1_ac6_ledger_states_the_eight_of_ten_anchor_floor(self) -> None:
        """The AC6 ledger text itself is pinned (the stated contract).

        The ledger states: rows each carry a named mitigation; rows with a
        runtime-testable outcome carry a P5 test anchor (8/10 — the two
        documentation-only rows carry none by design).
        """
        text = _read(PHASE1_PATH)
        assert "AC6: Failure-mode table rows each carry a named mitigation" in text, (
            "phase1-plan.md must carry the AC6 acceptance-criterion text"
        )
        assert "8/10" in text, (
            "the AC6 ledger must state its 8/10 anchor floor"
        )
        assert "the two documentation-only rows carry none by design" in text, (
            "the AC6 ledger must state that the two documentation-only rows "
            "carry no anchor by design"
        )

    def test_p1_br2_at_least_once_semantics_are_stated(self) -> None:
        """BR-2's at-least-once semantics are stated in the failure-modes section."""
        text = _read(PHASE1_PATH)
        match = re.search(
            r"^##\s+Failure modes\s*$(?P<body>.*?)(?=^##\s|\Z)",
            text,
            re.DOTALL | re.MULTILINE,
        )
        assert match, "phase1-plan.md must carry a '## Failure modes' section"
        body = match.group("body")
        for clause in (
            ">24h outage",
            "key-TTL expiry",
            "TOLERATED BY DESIGN",
            "Do **NOT** widen the substrate key TTL",
        ):
            assert clause in body, (
                f"the failure-mode table must state the BR-2 clause {clause!r} "
                f"(AC6 — the at-least-once semantics are stated IN the table)"
            )


# ---------------------------------------------------------------------------
# Row — P2 skill-version consistency (CITED; P2 T2.7 owns the count-pin).
# ---------------------------------------------------------------------------
SKILL_VERSION_CITATION_NODES: tuple[str, ...] = (
    "tests/unit/test_maintenancer_skill_versions_consistent.py::test_manifest_skill_set_is_exact",
    "tests/unit/test_maintenancer_skill_versions_consistent.py::test_manifest_set_equals_template_set",
)


class TestP2SkillVersionCitation:
    """P2 — the 6 lane skills + manifest version-consistency pin is CITED."""

    def test_skill_version_consistency_citation_nodes_exist_and_pass(self) -> None:
        """The cited version-consistency nodes exist in collection AND pass."""
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *SKILL_VERSION_CITATION_NODES,
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, (
            "the CITED skill-version consistency nodes must exist in "
            f"collection and pass; rc={completed.returncode}\n"
            f"{completed.stdout[-2000:]}"
        )


# ---------------------------------------------------------------------------
# The map's own self-consistency pin: every row has a named owning node.
# ---------------------------------------------------------------------------
class TestAcceptanceMapSelfConsistency:
    """The map itself is pinned — no orphan row, no stale citation."""

    def test_every_map_row_names_an_existing_test_node(self) -> None:
        """Each MAP_ROWS entry's owning node id must exist in the pin files."""
        inventory: set[str] = set()
        for relative in PIN_FILES:
            path = PROJECT_ROOT / relative
            assert path.exists(), f"the pin file {relative} is missing"
            inventory |= _test_functions(path)
        missing = [
            f"{row_id} -> {node}"
            for row_id, _label, node in MAP_ROWS
            if node not in inventory
        ]
        assert not missing, (
            "every acceptance-map row must name an existing test node; "
            f"dangling: {missing}"
        )

    def test_the_map_row_ids_are_unique(self) -> None:
        """Two rows may not share a row id."""
        row_ids = [row_id for row_id, _label, _node in MAP_ROWS]
        assert len(row_ids) == len(set(row_ids)), (
            f"duplicate acceptance-map row ids: {row_ids}"
        )

    def test_the_map_covers_every_verification_pass_row(self) -> None:
        """The map's row ids cover the phase5 verification-pass rows.

        Every BR named in the phase-5 verification-pass table is either
        mapped by THIS map (MAP_ROWS) or owned by a SIBLING pin file
        (SIBLING_OWNED_ROWS).  The BRs owned by the SIBLING P5 tasks
        (T5.1-T5.4, T5.6, T5.7) are out of this map's scope.
        """
        phase5 = _read(PROJECT_ROOT / ".agents" / "shared" / "planning" / "maintenance-system" / "phase5-plan.md")
        heading = "## Verification pass — P2/P3/P4 acceptance → check map"
        assert heading in phase5, (
            "phase5-plan.md must carry the verification-pass acceptance map"
        )
        section = phase5.split(heading, 1)[1]
        section = re.split(r"\n##\s", section, maxsplit=1)[0]
        source_rows = [
            line for line in section.splitlines() if line.strip().startswith("|")
        ]
        assert len(source_rows) > 10, (
            "the phase5 verification-pass table parsed to too few rows — "
            "the map section shape drifted"
        )
        br_labels = set()
        for line in source_rows:
            for br in re.findall(r"BR-\d+[a-z]?", line):
                br_labels.add(br)
        mapped = {row_id for row_id, _label, _node in MAP_ROWS}
        sibling_owned = {br for br, _owner in SIBLING_OWNED_ROWS}
        unmapped = sorted(
            br
            for br in br_labels
            if not any(re.search(rf"\b{re.escape(br)}\b", row_id) for row_id in mapped)
            and br not in sibling_owned
            and br not in SIBLING_P5_TASK_OWNED_BRS
        )
        assert not unmapped, (
            f"the acceptance map does not cover the phase5 verification-pass "
            f"BR rows: {unmapped}"
        )

    def test_the_sibling_owned_rows_point_at_real_pins(self) -> None:
        """Every sibling-owned row's pin file (and node) must exist."""
        for br, owner in SIBLING_OWNED_ROWS:
            file_part, _, node_part = owner.partition("::")
            path = PROJECT_ROOT / file_part
            assert path.exists(), (
                f"{br}'s sibling owner {file_part} does not exist"
            )
            if node_part:
                assert node_part in _test_functions(path), (
                    f"{br}'s sibling owner node {node_part} is not defined in "
                    f"{file_part} — the citation is stale"
                )
