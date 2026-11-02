"""P11 — no invented Plane MCP verbs on the charter surface.

Lane ``plane-pm-issue-tools`` Phase 4 (``phase4-plan.md`` §2.1 / §5 pin
**P11** / §6 exit criterion **X4.1** — G2 fold iter-003 blocker B2).

**The drift this pins.** The PM charter (and the daemon's own
``tool_name_prefix`` docstring) named verbs that **do not exist**: the drift
is at the ENTITY level — upstream's 177-verb catalog exposes **zero**
``*_issue`` verbs; the entity is ``work_item``. Pre-Phase-4 the charter
carried **seven distinct non-existent names across ten occurrences**
(G2 fold iter-003 numeric correction — the pre-fold "SIX" label was a
miscount).

**The rule (P11/X4.1 verbatim scope).** Every ``plane_*`` **MCP verb** on
the charter surface must be a member of the **gate-(i) re-captured
catalog**, evaluated against ``gate-i-keep-set.json`` + the committed
re-capture fixture (``tests/fixtures/plane_catalog.json``, loaded via
``tests/helpers/plane_catalog_fixture.py``). **THE EXPLICIT EXCLUSION SET
(three names, exact):** ``plane_sync_project`` (the daemon-native tool at
``daemon/tools/plane_sync.py`` — category ``plane_sync``; its mention in
``tools_note.md`` is mandated by §2.1 row 2), ``plane_sync`` (the non-tool
category identifier), ``plane_sync_state`` (the non-tool project-metadata
key). **These three are not catalog verbs and are not checked against the
catalog; no other identifier is exempt** — the exclusion set is not widened
by judgement.

**Surface scope note (recorded deviation).** The plan's P11 wording says
"the two daemon docstrings" — ``plane.py:143-152`` (``tool_name_prefix``)
and ``daemon/tools/plane_tools.py:14`` (``CATEGORY_DOC``). The Phase-4
dispatch fence permits **one** daemon docstring edit only
(``daemon/mcp/builtin_servers/plane.py``), so this check covers the four
PM prompt files + the ``tool_name_prefix`` docstring (extracted
structurally via ``ast`` — robust to line drift). The ``plane_tools.py``
``CATEGORY_DOC`` still names ``plane_list_issues`` and
``daemon/tools/mcp_tools.py:17`` still documents a nonexistent
``call_mcp_tool`` (plan §2.2 + O6) — both **routed** to the owning lane
(dispatch fence); widening this check to them would go RED on files this
phase may not touch.

**RED legs demonstrated here** (house discipline — a pin that cannot go RED
is vacuous): an invented verb planted in prompt text ⇒ the scanner reports
it; a widened exclusion set ⇒ the exact-three assertion fails; and the
repo-state assertions were captured RED against the pre-Phase-4 files
(seven distinct invented names) before the rewrite.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from tests.helpers.plane_catalog_fixture import catalog_names, load_catalog

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The charter surface: the four PM prompt files (whole files — they are
#: the prompt artifacts) + the single Phase-4-editable daemon docstring.
PM_PROMPT_FILES = (
    "agents/project-manager/tools_note.md",
    "agents/project-manager/rule.md",
    "agents/project-manager/workflow.md",
    "agents/project-manager/soul.md",
)
PLANE_PY = "daemon/mcp/builtin_servers/plane.py"
PLANE_PY_DOCSTRING_OWNER = "tool_name_prefix"  # §2.2 names :143-152

#: THE EXPLICIT EXCLUSION SET — exactly three names (P11/X4.1 verbatim).
#: ``plane_sync_project`` (daemon-native tool), ``plane_sync`` (category),
#: ``plane_sync_state`` (project-metadata key). NOT catalog verbs.
EXCLUDED_IDENTIFIERS = frozenset(
    {"plane_sync_project", "plane_sync", "plane_sync_state"}
)

#: An MCP-verb-shaped identifier: ``plane_`` + snake_case, ending
#: alphanumeric. The ``plane_*`` GLOB does not match (``*`` is not in the
#: class) and a bare ``plane_`` does not match either.
_VERB_TOKEN = re.compile(r"plane_[a-z][a-z0-9_]*[a-z0-9]")

_ISSUE_VERB = re.compile(r"plane_[a-z0-9_]*issue[a-z0-9_]*")


def allowed_daemon_names() -> frozenset[str]:
    """The gate-(i) catalog's daemon-side names ∪ the keep-set's.

    Evaluation input per P11: ``gate-i-keep-set.json`` AND the re-capture
    artifact (committed as ``tests/fixtures/plane_catalog.json``).
    """
    catalog = {"plane_" + raw for raw in catalog_names(load_catalog())}
    keep_set_path = (
        _REPO_ROOT
        / ".agents/shared/planning/plane-pm-issue-tools/gate-i-keep-set.json"
    )
    keep = json.loads(keep_set_path.read_text(encoding="utf-8"))
    keep_names = set(keep["keep_set_daemon_names"])
    # The keep-set must itself be a catalog subset (FC1 input integrity).
    assert keep_names <= catalog, (
        "gate-i-keep-set.json names a verb absent from the re-captured "
        "catalog (FC1 would fire): "
        f"{sorted(keep_names - catalog)}"
    )
    return frozenset(catalog | keep_names)


def plane_py_docstring() -> str:
    """The ``tool_name_prefix`` docstring, extracted structurally."""
    tree = ast.parse(
        (_REPO_ROOT / PLANE_PY).read_text(encoding="utf-8"),
        filename=PLANE_PY,
    )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == PLANE_PY_DOCSTRING_OWNER
        ):
            doc = ast.get_docstring(node)
            assert doc is not None, (
                f"{PLANE_PY}::{PLANE_PY_DOCSTRING_OWNER} lost its docstring"
            )
            return doc
    raise AssertionError(
        f"{PLANE_PY} no longer defines {PLANE_PY_DOCSTRING_OWNER!r} — "
        "P11's docstring anchor moved; update the structural extraction"
    )


def scan_text(
    label: str,
    text: str,
    allowed: frozenset[str],
    excluded: frozenset[str] = EXCLUDED_IDENTIFIERS,
) -> list[str]:
    """Violations for one text unit (file or docstring); empty ⇒ clean.

    ``excluded`` is injectable so the mutation legs can demonstrate what a
    widened exclusion set would mask — the repo-state tests pin the exact
    three-name set separately.
    """
    violations: list[str] = []
    for token in sorted(set(_VERB_TOKEN.findall(text))):
        if token in excluded:
            continue
        if token not in allowed:
            violations.append(
                f"{label}: invented Plane MCP verb {token!r} "
                "(not in the gate-(i) catalog)"
            )
        if _ISSUE_VERB.fullmatch(token):
            violations.append(
                f"{label}: {token!r} is an *_issue verb — the entity is "
                "work_item; zero *_issue verbs exist in the catalog"
            )
    return violations


def scan_repo_surface() -> list[str]:
    """Violations over the whole P11 surface (repo state)."""
    allowed = allowed_daemon_names()
    violations: list[str] = []
    for rel in PM_PROMPT_FILES:
        violations += scan_text(
            rel, (_REPO_ROOT / rel).read_text(encoding="utf-8"), allowed
        )
    violations += scan_text(
        f"{PLANE_PY}::{PLANE_PY_DOCSTRING_OWNER} (docstring)",
        plane_py_docstring(),
        allowed,
    )
    return violations


class TestPlanePromptVerbCheck:
    """P11 — repo-state assertions (were RED against the pre-Phase-4 files)."""

    def test_no_invented_verbs_on_the_charter_surface(self):
        violations = scan_repo_surface()
        assert violations == [], (
            "P11 violations:\n  - " + "\n  - ".join(violations)
        )

    def test_exclusion_set_is_exactly_three_names(self):
        """The exclusion set is an exact 3-name list — widening ⇒ RED."""
        assert EXCLUDED_IDENTIFIERS == frozenset(
            {"plane_sync_project", "plane_sync", "plane_sync_state"}
        ), (
            "the P11 exclusion set must stay EXACTLY the three mandated "
            "names (plane_sync_project / plane_sync / plane_sync_state) — "
            "no other identifier is exempt"
        )

    def test_excluded_names_are_not_catalog_verbs(self):
        """The three excluded names must NOT be catalog members.

        If upstream ever ships a real ``plane_sync_project`` MCP verb, the
        exclusion would silently mask it — this assertion makes that
        visible instead.
        """
        allowed = allowed_daemon_names()
        overlap = EXCLUDED_IDENTIFIERS & allowed
        assert overlap == set(), (
            f"excluded name(s) became catalog verbs: {sorted(overlap)}"
        )

    def test_charter_names_the_reconciled_core_verbs(self):
        """Positive control — the rewrite actually landed the vocabulary.

        A scanner that only rejects unknown verbs would stay green on a
        charter that names NO verbs at all; this pins the keep-set core
        (work-item CRUD + comments + cycles + assignee) as present in
        ``tools_note.md``.
        """
        text = (
            _REPO_ROOT / "agents/project-manager/tools_note.md"
        ).read_text(encoding="utf-8")
        for verb in (
            "plane_create_work_item",
            "plane_update_work_item",
            "plane_delete_work_item",
            "plane_list_work_items",
            "plane_create_work_item_comment",
            "plane_complete_cycle",
            "plane_manage_work_item_assignee",
            "plane_sync_project",  # the mandated ownership mention (§2.1 row 2)
        ):
            assert verb in text, f"tools_note.md no longer names {verb}"


class TestP11MutationLegs:
    """RED legs on synthetic text — the check can still fail, forever."""

    @staticmethod
    def _clean_text() -> str:
        return (
            "I read Plane work items with `plane_list_work_items` and "
            "create them with `plane_create_work_item`; comments via "
            "`plane_create_work_item_comment`; the worker holds "
            "`plane_sync_project` (category `plane_sync`; state key "
            "`plane_sync_state`).\n"
        )

    def test_clean_text_is_green(self):
        allowed = allowed_daemon_names()
        assert scan_text("synthetic", self._clean_text(), allowed) == []

    def test_invented_verb_planted_goes_red(self):
        """The classic invented name reintroduced ⇒ RED (both clauses)."""
        allowed = allowed_daemon_names()
        text = self._clean_text() + "and issues via `plane_create_issue`."
        violations = scan_text("synthetic", text, allowed)
        assert any("invented Plane MCP verb 'plane_create_issue'" in v
                   for v in violations), violations
        assert any("*_issue verb" in v for v in violations), violations

    def test_invented_non_issue_verb_goes_red(self):
        """An invented verb that is NOT *_issue-shaped must also fail."""
        allowed = allowed_daemon_names()
        text = self._clean_text() + "or `plane_teleport_work_item`."
        violations = scan_text("synthetic", text, allowed)
        assert any(
            "invented Plane MCP verb 'plane_teleport_work_item'" in v
            for v in violations
        ), violations

    def test_widened_exclusion_masks_the_invented_verb(self):
        """What a widened exclusion set would do — and why it is pinned.

        With a FOURTH name added to the exclusion set, the invented verb
        sails through the scanner. The repo-state exact-three assertion
        (test_exclusion_set_is_exactly_three_names) is what makes that
        widening impossible without going RED here first.
        """
        allowed = allowed_daemon_names()
        widened = EXCLUDED_IDENTIFIERS | {"plane_create_issue"}
        text = self._clean_text() + "and issues via `plane_create_issue`."
        masked = scan_text("synthetic", text, allowed, excluded=widened)
        assert masked == [], (
            "expected the widened exclusion to mask the invented verb — "
            "the masking demonstration itself broke"
        )
        assert widened != EXCLUDED_IDENTIFIERS, (
            "the widened set must differ from the pinned three-name set"
        )
