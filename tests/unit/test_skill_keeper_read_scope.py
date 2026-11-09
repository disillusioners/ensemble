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
