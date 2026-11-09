"""B3 — the opt-in, default-OFF per-agent READ SCOPE (Phase-1 §6 B3, DS-15).

The residual the confinement lane left open: the skill-keeper holds a
READ-ONLY filesystem surface (``read_file`` / ``list_directory`` /
``glob_files`` / ``grep_files``) but ``read_file`` and
``list_directory`` resolve ABSOLUTE paths outside the workdir (the
absolute-path skip in ``_resolve_within_workdir``). That reach covers the
repo ``.env``, ``~/.ssh/*``, ``/proc/self/environ`` and any other
agent's directory — the credential-hazard class D3 records as
*"residual (read side)"*.

``glob_files`` / ``grep_files`` are ALREADY fenced (``_resolve_search_root``
refuses an absolute root outside the workdir/temp dirs), so the open call
sites are exactly ``read_file`` and ``list_directory``.

Posture:
* **Opt-in, default-OFF** — a scope is honored only when the agent
  DECLARES one in ``agents/<name>/meta.json``. Absent declaration ⇒
  every other agent's read behavior is byte-identical to base. The
  fleet-wide default stays deferred (D8 res.9).
* **Keeper-only in Phase 1** — ``skill-keeper`` declares
  ``"read_scope": "project"``; no other agent declares one.
* **Not agent-controllable** — the binding is made by the tool FACTORY
  (the calling agent's identity), never by the tool's arguments. An
  agent can neither widen its own scope nor pass a forged one.
* **Write-path scope is OUT OF SCOPE** (plan §6 B3) — ``write_file`` /
  ``edit_file`` resolution is untouched. The keeper holds no write
  tool, so a write-path fence would defend an unreachable surface.

The production tool factory is driven for real here: the tools are
built by ``create_instance_tools(manager, instance_id, agent_id=...)``
and the read tools are the SAME wrappers the runtime binds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
KEEPER_META_PATH = AGENTS_DIR / "skill-keeper" / "meta.json"


def _make_manager(workdir: str) -> Any:
    """A manager mock shaped like the real one for the tool factory.

    ``_get_project_workdir`` reads the instance's project
    ``main_directory`` — that is the workdir the read scope is bounded
    to.
    """
    manager = MagicMock()
    manager.config = SimpleNamespace(llm=SimpleNamespace(allowed_models=[]))
    instance_meta = MagicMock()
    instance_meta.project_id = "proj-scope"
    manager._instance_repository.get = MagicMock(return_value=instance_meta)
    project = MagicMock()
    project.main_directory = workdir
    manager._project_repository.get = MagicMock(return_value=project)
    return manager


def _build_tools(agent_id: str, workdir: str) -> dict[str, Any]:
    """Build the PRODUCTION tool set for ``agent_id``."""
    from daemon.tools.instance import create_instance_tools

    manager = _make_manager(workdir)
    tools = create_instance_tools(manager, "inst-scope", agent_id=agent_id)
    return {getattr(t, "name", None): t for t in tools}


@pytest.fixture
def workdir(tmp_path: Path) -> str:
    """A scratch project workdir with one readable file inside it."""
    root = tmp_path / "project"
    (root / "agents" / "skill-keeper").mkdir(parents=True)
    (root / "agents" / "skill-keeper" / "meta.json").write_text(
        json.dumps({"id": "skill-keeper"}), encoding="utf-8"
    )
    (root / "inside.txt").write_text("inside-the-workdir\n", encoding="utf-8")
    return str(root)


@pytest.fixture(autouse=True)
def _clear_scope_cache():
    """The declared-scope cache is module-level; keep legs independent."""
    from daemon.tools import filesystem

    filesystem._READ_SCOPE_CACHE.clear()
    yield
    filesystem._READ_SCOPE_CACHE.clear()


# =============================================================================
# The declaration
# =============================================================================


class TestReadScopeDeclaration:
    def test_the_keeper_declares_the_project_scope(self):
        meta = json.loads(KEEPER_META_PATH.read_text(encoding="utf-8"))
        assert meta.get("read_scope") == "project", (
            "skill-keeper must declare the bounded 'project' read scope"
        )

    def test_the_meta_version_was_bumped(self):
        """A behavior change ⇒ the meta version must move."""
        meta = json.loads(KEEPER_META_PATH.read_text(encoding="utf-8"))
        assert meta.get("version") != "0.2.0"

    def test_no_other_agent_declares_a_read_scope(self):
        """The default-OFF pin (M-B3c's static half).

        Phase 1 declares the scope for skill-keeper ONLY. A second
        declaration would put the fleet back in the deferred territory
        (D8 res.9) without the design that territory needs.
        """
        declared = []
        for meta_path in sorted(AGENTS_DIR.glob("*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            scope = meta.get("read_scope")
            if scope is None:
                tools_cfg = meta.get("tools")
                if isinstance(tools_cfg, dict):
                    scope = tools_cfg.get("read_scope")
            if scope is not None:
                declared.append((meta_path.parent.name, scope))
        assert declared == [("skill-keeper", "project")], (
            f"only skill-keeper may declare a read scope in Phase 1: {declared}"
        )

    def test_the_declared_scope_is_readable_from_the_meta_json(self):
        from daemon.tools.filesystem import declared_read_scope

        assert declared_read_scope("skill-keeper") == "project"
        assert declared_read_scope("coder") is None

    def test_an_undeclared_agent_has_no_scope(self):
        from daemon.tools.filesystem import declared_read_scope

        assert declared_read_scope("") is None
        assert declared_read_scope(None) is None
        assert declared_read_scope("no-such-agent-xyz") is None


# =============================================================================
# The enforcement — the keeper's read reach
# =============================================================================


class TestKeeperReadScopeEnforcement:
    def test_read_file_refuses_an_absolute_path_outside_the_workdir(self, workdir):
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "/etc/passwd", "workdir": workdir}
        )
        assert result.startswith("ERROR:"), (
            f"the keeper must not read outside its project: {result!r}"
        )
        assert "read scope" in result

    def test_read_file_refuses_the_repo_dotenv(self, workdir):
        """The credential hazard the scope exists for."""
        tools = _build_tools("skill-keeper", workdir)
        repo_env = str(REPO_ROOT / ".env")
        result = tools["read_file"].invoke({"path": repo_env, "workdir": workdir})
        assert result.startswith("ERROR:"), (
            f"the keeper must not read the repo .env: {result!r}"
        )

    def test_read_file_refuses_proc_self_environ(self, workdir):
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "/proc/self/environ", "workdir": workdir}
        )
        assert result.startswith("ERROR:"), (
            f"the keeper must not read /proc/self/environ: {result!r}"
        )

    def test_read_file_refuses_an_absolute_path_outside_the_workdir_without_a_workdir_argument(
        self, workdir
    ):
        """The plan's acceptance list, in PRODUCTION shape.

        The plan requires ``read_file("/etc/passwd")`` to be REFUSED.
        This leg passes NO ``workdir`` argument at all — the daemon
        resolves the anchor itself — so the refusal can only come from
        the SCOPE, not from the argument handling.
        """
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke({"path": "/etc/passwd"})
        assert result.startswith("ERROR:"), (
            f"the keeper must not read /etc/passwd: {result!r}"
        )
        assert "read scope" in result

    def test_read_file_allows_a_relative_in_workdir_path(self, workdir):
        """Relative in-workdir reads stay LEGAL (M-B3b's leg).

        The anchor is the daemon-resolved project workdir — a scoped
        agent passes a RELATIVE path and the daemon resolves it.
        """
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke({"path": "inside.txt"})
        assert "inside-the-workdir" in result, (
            f"a relative in-workdir read must still work: {result!r}"
        )

    def test_read_file_refuses_an_absolute_in_workdir_path(self, workdir):
        """D1 ruling 3 — an ABSOLUTE path is refused for a scoped agent.

        The plan's acceptance list requires ``<repo>/.env`` to be
        refused even though the repo IS the anchor, so the rule is
        "RELATIVE reads only", not "inside the anchor".
        """
        tools = _build_tools("skill-keeper", workdir)
        target = os.path.join(workdir, "inside.txt")
        result = tools["read_file"].invoke({"path": target})
        assert result.startswith("ERROR:"), (
            f"an absolute path must be refused for a scoped agent: {result!r}"
        )
        assert "RELATIVE" in result

    def test_read_file_allows_the_keepers_own_meta_relative(self, workdir):
        """The acceptance example: the keeper reads its own meta.json."""
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "agents/skill-keeper/meta.json"}
        )
        assert result.startswith("File:"), (
            f"the keeper must still read its own meta.json: {result!r}"
        )

    def test_list_directory_refuses_an_absolute_path_outside_the_workdir(self, workdir):
        tools = _build_tools("skill-keeper", workdir)
        result = tools["list_directory"].invoke({"path": "/etc", "workdir": workdir})
        assert result.startswith("ERROR:"), (
            f"the keeper must not list outside its project: {result!r}"
        )

    def test_list_directory_allows_a_relative_in_workdir_path(self, workdir):
        tools = _build_tools("skill-keeper", workdir)
        result = tools["list_directory"].invoke(
            {"path": "agents/skill-keeper"}
        )
        assert "meta.json" in result, (
            f"a relative in-workdir listing must still work: {result!r}"
        )

    def test_the_scope_is_not_agent_controllable(self, workdir):
        """The scope is bound by the FACTORY, never by tool arguments."""
        tools = _build_tools("skill-keeper", workdir)
        schema = tools["read_file"].args_schema
        fields = set(getattr(schema, "model_fields", {}) or {})
        assert not any("scope" in field for field in fields), (
            f"the read tool must not expose a scope argument: {sorted(fields)}"
        )
        # And a forged argument does not widen the reach.
        result = tools["read_file"].invoke(
            {"path": "/etc/passwd", "read_scope": "none"}
        )
        assert result.startswith("ERROR:"), (
            f"a forged scope argument must not widen the reach: {result!r}"
        )


# =============================================================================
# D1 — the ANCHOR is trusted, the ``workdir`` ARGUMENT is not
# =============================================================================
#
# The reviewed bypass (Phase-1 fix round 1, D1): the scope's base was
# ``Path(workdir).resolve()`` — and ``workdir`` is a FIRST-CLASS,
# AGENT-SUPPLIED tool argument. The reviewer drove the production shape
# and every probe returned the file:
#
#   [P1] workdir=<repo>      read_file(<repo>/.env)          -> RETURNED
#   [P2] workdir="/"          read_file("/etc/passwd")       -> RETURNED
#   [P3] workdir="/proc/self" read_file("/proc/self/environ")-> RETURNED
#   [P4] list_directory(path="/", workdir="/")               -> RETURNED the FS root
#
# The ruling: the base MUST be the daemon-resolved trusted workdir, the
# agent-supplied ``workdir`` MUST NOT move it, and an ABSOLUTE path
# argument MUST be refused for a scoped agent.


class TestTrustedAnchor:
    def test_read_file_refuses_an_agent_supplied_root_workdir(self, workdir):
        """[P2] — the D1 bypass, closed."""
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "/etc/passwd", "workdir": "/"}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not move its read anchor: {result!r}"
        )
        assert "workdir" in result

    def test_read_file_refuses_an_agent_supplied_repo_workdir(self, workdir):
        """[P1] — the D1 bypass, closed (the repo-``.env`` shape)."""
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": str(REPO_ROOT / ".env"), "workdir": str(REPO_ROOT)}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not move its read anchor: {result!r}"
        )

    def test_read_file_refuses_an_agent_supplied_proc_workdir(self, workdir):
        """[P3] — the D1 bypass, closed."""
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "/proc/self/environ", "workdir": "/proc/self"}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not move its read anchor: {result!r}"
        )

    def test_list_directory_refuses_an_agent_supplied_root_workdir(self, workdir):
        """[P4] — the D1 bypass, closed."""
        tools = _build_tools("skill-keeper", workdir)
        result = tools["list_directory"].invoke({"path": "/", "workdir": "/"})
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not list the filesystem root: {result!r}"
        )

    def test_a_relative_escape_cannot_leave_the_anchor(self, workdir):
        """The relative form of the D1 bypass — the anchor is not movable.

        A relative path that climbs out of the anchor must be refused.
        The agent-supplied ``workdir="/"`` must not become the base: if
        it did, ``../../etc/passwd`` would resolve to ``/etc/passwd``
        and be handed to the agent. This is the probe the M-B3e leg
        (the D1 anchor defect) must redden.
        """
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "../../etc/passwd", "workdir": "/"}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not climb out of the trusted anchor: {result!r}"
        )

    def test_the_read_anchor_is_the_daemon_resolved_project_workdir(self, workdir):
        """The anchor is the DAEMON's workdir — the manager's project dir."""
        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke({"path": "inside.txt"})
        assert "inside-the-workdir" in result, (
            f"the relative read must resolve at the trusted anchor: {result!r}"
        )

    def test_a_relative_read_resolves_at_the_anchor_not_at_the_argument(
        self, workdir, tmp_path
    ):
        """The argument cannot become the base — even for a relative path.

        The trusted anchor holds ``inside.txt``; a DIFFERENT directory
        (the argument) also holds a file with the same name. The
        agent-supplied ``workdir`` is refused outright, so the read can
        only resolve at the anchor.
        """
        other = tmp_path / "elsewhere"
        other.mkdir()
        (other / "inside.txt").write_text("the-other-file\n", encoding="utf-8")

        tools = _build_tools("skill-keeper", workdir)
        result = tools["read_file"].invoke(
            {"path": "inside.txt", "workdir": str(other)}
        )
        assert result.startswith("ERROR:"), (
            f"an agent-supplied workdir must be refused outright: {result!r}"
        )

    def test_the_trusted_anchor_never_comes_from_the_tool_arguments(self):
        """Static pin — the resolver must not read the ``workdir`` argument.

        The base is the CONTEXTVAR the factory binds. A resolver that
        took the base from the tool-call's ``workdir`` would be the D1
        defect itself. The check is AST-level: the scoped resolver must
        not even NAME a ``workdir`` variable (the word may appear in a
        message, never as a binding).
        """
        import ast
        import inspect

        from daemon.tools import filesystem

        sig = inspect.signature(filesystem._resolve_within_scope)
        assert "workdir" not in sig.parameters, (
            "the scoped resolver must not take the tool-call's workdir "
            f"argument: {sorted(sig.parameters)}"
        )

        tree = ast.parse(inspect.getsource(filesystem._resolve_within_scope))
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        assert "workdir" not in names, (
            "the scoped resolver must not read a workdir binding: "
            f"{sorted(names)}"
        )

        anchor_source = inspect.getsource(filesystem._trusted_scope_anchor)
        assert "current_trusted_workdir()" in anchor_source, (
            "the scoped resolver's base must come from the trusted-workdir "
            "binding the factory sets"
        )


# =============================================================================
# The default-OFF guarantee — every other agent is byte-identical
# =============================================================================


class TestOtherAgentsAreUnchanged:
    def test_another_agent_reads_an_absolute_path_outside_the_workdir(self, workdir):
        """The no-fleet-change pin (M-B3c).

        An agent that declares NO scope keeps today's behavior: an
        absolute path outside the workdir is still resolved.
        """
        tools = _build_tools("coder", workdir)
        result = tools["read_file"].invoke(
            {"path": "/etc/hostname", "workdir": workdir}
        )
        assert result.startswith("File:"), (
            f"a non-declaring agent's read must be unchanged: {result!r}"
        )

    def test_another_agent_lists_an_absolute_path_outside_the_workdir(self, workdir):
        tools = _build_tools("coder", workdir)
        result = tools["list_directory"].invoke({"path": "/etc", "workdir": workdir})
        assert not result.startswith("ERROR:"), (
            f"a non-declaring agent's listing must be unchanged: {result!r}"
        )

    def test_the_write_path_resolution_is_untouched(self, workdir):
        """Write-path scope is explicitly OUT OF SCOPE (plan §6 B3).

        The resolver's write/edit call path passes no scope agent id, so
        its behavior is byte-identical to the pre-scope resolver.
        """
        from daemon.tools.filesystem import _resolve_within_workdir

        target, err = _resolve_within_workdir("/etc/hostname", workdir)
        assert err is None
        assert target is not None and str(target) == "/etc/hostname"

    def test_the_scope_argument_is_opt_in_at_the_resolver(self, workdir):
        """The resolver's scope hook is opt-in per call site."""
        from daemon.tools.filesystem import _resolve_within_workdir

        target, err = _resolve_within_workdir("/etc/hostname", workdir)
        assert err is None and target is not None


# =============================================================================
# D-N1 — the WALK tools (glob_files / grep_files) carry the SAME scope
# =============================================================================
#
# The re-adjudication PROVED content exfiltration on the keeper's own bound
# surface: `grep_files(pattern=<canary>, path=<out-of-anchor>, workdir="/")`
# returned the canary, and `glob_files(..., path=<foreign tree>, workdir="/")`
# listed files — while `read_file` / `list_directory` on the same paths were
# correctly REFUSED. The plan's premise ("glob/grep already fenced") was FALSE
# for a scoped agent: `_resolve_search_root` bounded the walk against the
# AGENT-SUPPLIED `workdir` — the same anchor bug D1 had.
#
# The ruling: the SAME `_make_read_scope_aware` binding and the SAME
# scoped-agent policy as the file tools — (1) a supplied `workdir` is
# REFUSED, (2) an ABSOLUTE `path` is REFUSED, (3) a relative `path` resolves
# against the trusted anchor and cannot escape it, (4) undeclared agents are
# byte-identical to base.
#
# The canary fixture keeps CONTENT out of every assertion: the pins assert
# only on STATUS (refused / no-match / found), never on file bytes.

CANARY_TOKEN = "CANARY_TOKEN_DN1_9f3a"


@pytest.fixture
def canary_tree(tmp_path: Path) -> dict[str, str]:
    """An anchor with an ordinary file, plus a canary tree OUTSIDE it."""
    anchor = tmp_path / "project"
    anchor.mkdir()
    (anchor / "inside.txt").write_text("inside-the-anchor\n", encoding="utf-8")
    (anchor / "agents" / "skill-keeper").mkdir(parents=True)
    (anchor / "agents" / "skill-keeper" / "meta.json").write_text(
        json.dumps({"id": "skill-keeper"}), encoding="utf-8"
    )
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "canary.txt").write_text(
        f"canary line {CANARY_TOKEN}\n", encoding="utf-8"
    )
    (outside / "deep" / "nested.txt").write_text(
        f"nested line {CANARY_TOKEN}\n", encoding="utf-8"
    )
    return {"anchor": str(anchor), "outside": str(outside)}


def _leaks(result: str) -> bool:
    """True when a result carries the canary token (i.e. exfiltration)."""
    return CANARY_TOKEN in result


class TestWalkToolsAreScoped:
    """glob_files / grep_files obey the scoped-agent policy."""

    # -- (2) an ABSOLUTE path argument is REFUSED --------------------------

    def test_grep_refuses_an_absolute_path_outside_the_anchor(self, canary_tree):
        """The reviewer's exfiltration probe — closed."""
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["grep_files"].invoke(
            {"pattern": CANARY_TOKEN, "path": canary_tree["outside"]}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not grep an absolute out-of-anchor path: {result!r}"
        )
        assert not _leaks(result), "the canary token must NEVER be returned"

    def test_glob_refuses_an_absolute_path_outside_the_anchor(self, canary_tree):
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["glob_files"].invoke(
            {"pattern": "*.txt", "path": canary_tree["outside"]}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not glob an absolute out-of-anchor path: {result!r}"
        )

    def test_grep_refuses_an_absolute_path_inside_the_anchor(self, canary_tree):
        """Ruling property (2) is unconditional: absolute ⇒ refused."""
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["grep_files"].invoke(
            {"pattern": "inside-the-anchor", "path": canary_tree["anchor"]}
        )
        assert result.startswith("ERROR:"), (
            f"an absolute walk root must be refused for a scoped agent: {result!r}"
        )

    # -- (1) a supplied workdir is REFUSED --------------------------------

    def test_grep_refuses_an_agent_supplied_workdir(self, canary_tree):
        """The D-N1 anchor bug: `workdir="/"` must not widen the walk."""
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["grep_files"].invoke(
            {"pattern": CANARY_TOKEN, "path": canary_tree["outside"], "workdir": "/"}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not move its walk anchor: {result!r}"
        )
        assert not _leaks(result)

    def test_glob_refuses_an_agent_supplied_workdir(self, canary_tree):
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["glob_files"].invoke(
            {"pattern": "*.txt", "path": canary_tree["outside"], "workdir": "/"}
        )
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not move its walk anchor: {result!r}"
        )

    # -- (3) relative paths resolve at the anchor and cannot escape -------

    def test_grep_allows_a_relative_in_anchor_path(self, canary_tree):
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["grep_files"].invoke(
            {"pattern": "inside-the-anchor", "path": "."}
        )
        assert not result.startswith("ERROR:"), (
            f"a relative in-anchor grep must still work: {result!r}"
        )
        assert "inside.txt" in result

    def test_glob_allows_a_relative_in_anchor_path(self, canary_tree):
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["glob_files"].invoke({"pattern": "*.txt", "path": "."})
        assert "inside.txt" in result, (
            f"a relative in-anchor glob must still work: {result!r}"
        )
        assert "canary.txt" not in result

    def test_grep_refuses_a_relative_escape(self, canary_tree):
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["grep_files"].invoke(
            {"pattern": CANARY_TOKEN, "path": "../outside"}
        )
        assert result.startswith("ERROR:"), (
            f"a relative walk root must not escape the anchor: {result!r}"
        )
        assert not _leaks(result)

    def test_glob_refuses_a_relative_escape(self, canary_tree):
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        result = tools["glob_files"].invoke(
            {"pattern": "*.txt", "path": "../outside"}
        )
        assert result.startswith("ERROR:"), (
            f"a relative walk root must not escape the anchor: {result!r}"
        )

    # -- the CONTENT probe ------------------------------------------------

    def test_no_content_exfiltration_via_any_walk_vector(self, canary_tree):
        """CONTENT probe: no walk vector returns the canary token.

        Every out-of-anchor shape the reviewer drove is attempted. The
        assertion is on CONTENT, not on the refusal text: EVEN IF a future
        refactor lets a call through, the canary must not come back.
        """
        tools = _build_tools("skill-keeper", canary_tree["anchor"])
        outside = canary_tree["outside"]
        anchor = canary_tree["anchor"]
        vectors = [
            ("grep_files", {"pattern": CANARY_TOKEN, "path": outside}),
            ("grep_files", {"pattern": CANARY_TOKEN, "path": outside, "workdir": "/"}),
            ("grep_files", {"pattern": CANARY_TOKEN, "path": outside, "workdir": outside}),
            ("grep_files", {"pattern": CANARY_TOKEN, "path": "../outside"}),
            ("grep_files", {"pattern": CANARY_TOKEN, "path": ".", "workdir": outside}),
            ("glob_files", {"pattern": "*.txt", "path": outside}),
            ("glob_files", {"pattern": "*.txt", "path": outside, "workdir": "/"}),
            ("glob_files", {"pattern": "**/*", "path": anchor, "workdir": outside}),
            ("glob_files", {"pattern": "*.txt", "path": "../outside"}),
        ]
        for tool_name, args in vectors:
            result = tools[tool_name].invoke(args)
            assert not _leaks(result), (
                f"{tool_name} LEAKED the canary for {args!r}: {result!r}"
            )

    # -- (4) undeclared agents are byte-identical to base ------------------

    def test_another_agent_still_walks_outside_the_workdir(self, canary_tree):
        """The no-fleet-change pin: `coder` keeps base walk behavior."""
        tools = _build_tools("coder", canary_tree["anchor"])
        result = tools["grep_files"].invoke(
            {"pattern": CANARY_TOKEN, "path": canary_tree["outside"]}
        )
        assert not result.startswith("ERROR:"), (
            f"a non-declaring agent's walk must be unchanged: {result!r}"
        )
        assert _leaks(result), (
            "the base behavior for an undeclared agent is to return the match"
        )

    def test_another_agent_still_globs_outside_the_workdir(self, canary_tree):
        tools = _build_tools("coder", canary_tree["anchor"])
        result = tools["glob_files"].invoke(
            {"pattern": "*.txt", "path": canary_tree["outside"]}
        )
        assert "canary.txt" in result, (
            f"a non-declaring agent's glob must be unchanged: {result!r}"
        )


# =============================================================================
# D-N2 — the credential-hazard basename rule (`.env` family)
# =============================================================================
#
# The anchor IS the project repo, so "relative-only" alone permits exactly
# the file the scope exists to deny: `read_file(".env")` RETURNED the live
# credential block (619 chars) in the re-adjudication. DS-15 names this
# class explicitly. This is the NAMED EXCEPTION for that exact hazard —
# not a broad denylist; the primary fence remains relative-only + the
# trusted anchor.


class TestCredentialBasenameRule:
    def test_the_denied_basename_constant_exists(self):
        from daemon.tools import filesystem

        assert ".env" in filesystem._SCOPE_DENIED_BASENAMES

    def test_read_file_refuses_the_repo_dotenv_relative(self, dotenv_tree):
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["read_file"].invoke({"path": ".env"})
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not read the repo .env: {result!r}"
        )
        assert not _leaks(result)

    def test_read_file_refuses_a_nested_dotenv(self, dotenv_tree):
        """The rule is by BASENAME, at ANY depth under the anchor."""
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["read_file"].invoke({"path": "frontend/.env"})
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not read a nested .env: {result!r}"
        )
        assert not _leaks(result)

    def test_read_file_refuses_a_dotenv_family_member(self, dotenv_tree):
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["read_file"].invoke({"path": ".env.local"})
        assert result.startswith("ERROR:"), (
            f"a scoped agent must not read .env.local: {result!r}"
        )
        assert not _leaks(result)

    def test_read_file_still_allows_an_ordinary_relative_file(self, dotenv_tree):
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["read_file"].invoke({"path": "pyproject.toml"})
        assert result.startswith("File:"), (
            f"an ordinary relative read must stay legal: {result!r}"
        )

    def test_glob_yields_nothing_for_dotenv_under_scope(self, dotenv_tree):
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["glob_files"].invoke({"pattern": "**/.env*", "path": "."})
        # The PATTERN is echoed in the no-match notice, so assert on the
        # RESULT ROWS (line-equal), never on a substring of the whole text.
        rows = {line.strip() for line in result.splitlines()}
        assert not any(row == ".env" or row.startswith(".env.") or
                       row.endswith("/.env") for row in rows), (
            f"a scoped glob must not enumerate .env names: {result!r}"
        )
        assert result.startswith("No files matching"), (
            f"a scoped glob for the .env family must find NOTHING: {result!r}"
        )

    def test_grep_yields_no_content_for_dotenv_under_scope(self, dotenv_tree):
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["grep_files"].invoke(
            {"pattern": "sk-dotenv-secret-marker", "path": "."}
        )
        # The PATTERN is echoed in the no-match notice — assert on the
        # match SHAPE (a `path:line: content` row), never on a substring.
        assert result.startswith("No matches"), (
            f"a scoped grep must not return .env content: {result!r}"
        )
        assert ":1:" not in result, (
            f"a scoped grep returned a match ROW: {result!r}"
        )

    def test_another_agent_reads_the_dotenv_unchanged(self, dotenv_tree):
        """Base behavior for an undeclared agent is UNCHANGED."""
        tools = _build_tools("coder", dotenv_tree)
        result = tools["read_file"].invoke(
            {"path": ".env", "workdir": dotenv_tree}
        )
        assert result.startswith("File:"), (
            f"a non-declaring agent's .env read must be unchanged: {result!r}"
        )


@pytest.fixture
def dotenv_tree(tmp_path: Path) -> str:
    """An anchor holding the `.env` family plus one ordinary file."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "agents" / "skill-keeper").mkdir(parents=True)
    (root / "agents" / "skill-keeper" / "meta.json").write_text(
        json.dumps({"id": "skill-keeper"}), encoding="utf-8"
    )
    (root / ".env").write_text(
        "OPENAI_API_KEY=sk-dotenv-secret-marker\n", encoding="utf-8"
    )
    (root / "frontend").mkdir()
    (root / "frontend" / ".env").write_text(
        "VITE_TOKEN=sk-dotenv-secret-marker\n", encoding="utf-8"
    )
    (root / ".env.local").write_text(
        "LOCAL=sk-dotenv-secret-marker\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='fixture'\n", encoding="utf-8"
    )
    return str(root)


# =============================================================================
# D-N7 — `list_directory` must not ENUMERATE denied basenames
# =============================================================================
#
# The `.env` fix closed the READ surface; the listing surface was the
# sibling it left open. `list_directory(show_hidden=True)` still printed
# `.env` / `.env.local` — names only, never content, but the NAME is
# itself the hazard: it tells the agent the credential file exists and
# where. The fix filters entries through `_is_scope_denied_basename` for
# a SCOPED agent only; undeclared agents are byte-identical to base.


class TestListDirectoryDoesNotEnumerateDeniedBasenames:
    def test_a_scoped_agent_does_not_see_dotenv_names_with_show_hidden(
        self, dotenv_tree
    ):
        """The reviewer's probe: `show_hidden=True` enumerated `.env`."""
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["list_directory"].invoke(
            {"path": ".", "show_hidden": True}
        )
        rows = {line.strip() for line in result.splitlines() if line.strip()}
        for denied in (".env", ".env.local"):
            assert denied not in rows, (
                f"a scoped agent must not see the denied name {denied!r}: {result!r}"
            )

    def test_a_scoped_agent_does_not_see_dotenv_names_without_show_hidden(
        self, dotenv_tree
    ):
        """The rule is not a `show_hidden` consequence — it holds always."""
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["list_directory"].invoke(
            {"path": ".", "show_hidden": False}
        )
        rows = {line.strip() for line in result.splitlines() if line.strip()}
        assert ".env" not in rows and ".env.local" not in rows, (
            f"a scoped agent must not see the denied names: {result!r}"
        )

    def test_a_scoped_agent_does_not_see_a_nested_dotenv(self, dotenv_tree):
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["list_directory"].invoke(
            {"path": "frontend", "show_hidden": True}
        )
        rows = {line.strip() for line in result.splitlines() if line.strip()}
        assert ".env" not in rows, (
            f"a nested `.env` must not be enumerated: {result!r}"
        )

    def test_a_scoped_agent_still_lists_ordinary_entries(self, dotenv_tree):
        """Non-vacuity: the filter must not empty the listing."""
        tools = _build_tools("skill-keeper", dotenv_tree)
        result = tools["list_directory"].invoke(
            {"path": ".", "show_hidden": True}
        )
        assert "pyproject.toml" in result, (
            f"ordinary entries must still be listed: {result!r}"
        )

    def test_an_undeclared_agent_still_sees_the_dotenv_name(self, dotenv_tree):
        """The unchanged-behavior twin — base listing is untouched."""
        tools = _build_tools("coder", dotenv_tree)
        result = tools["list_directory"].invoke(
            {"path": ".", "workdir": dotenv_tree, "show_hidden": True}
        )
        rows = {line.strip() for line in result.splitlines() if line.strip()}
        assert ".env" in rows, (
            f"a non-declaring agent's listing must be unchanged: {result!r}"
        )
        assert ".env.local" in rows


# =============================================================================
# D-N8 — the CATEGORY_DOC states the REAL contract
# =============================================================================
#
# DOC-ONLY: zero behavior change. The pin exists because the old text
# told agents `workdir` is REQUIRED for absolute-path walks — which
# contradicts the shipped scoped-agent policy (an explicit `workdir` is
# REJECTED there; the anchor is factory-bound). A wrong doc is a wrong
# instruction to every agent that reads the category help.


class TestCategoryDocStatesTheRealContract:
    def test_the_doc_describes_the_scoped_agent_policy(self):
        from daemon.tools import filesystem

        doc = filesystem.CATEGORY_DOC
        assert "Scoped agents" in doc, (
            "the category doc must describe the scoped-agent read policy"
        )
        assert "daemon-resolved trusted workdir" in doc, (
            "the doc must name the trusted anchor as the read base"
        )
        assert "not accepted" in doc, (
            "the doc must state that the `workdir` argument is refused for "
            "a scoped agent"
        )
        assert ".env" in doc, (
            "the doc must name the credential-hazard basename rule"
        )

    def test_the_doc_keeps_the_undeclared_agent_semantics(self):
        from daemon.tools import filesystem

        doc = filesystem.CATEGORY_DOC
        assert "Undeclared agents" in doc, (
            "the legacy (undeclared) semantics must stay documented — the "
            "fleet default is unchanged"
        )
