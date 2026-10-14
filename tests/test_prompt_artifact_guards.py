"""Phase A T9/T11 immutability-guard tests.

Covers:
- T9(a) — filesystem.write_file + edit_file refuse absolute-path and
  relative-path writes against the versioned-immutable set (soul.md,
  rule.md, workflow.md, tools_note.md, tools.md, skill.md).
- T9(a) — read_file / list_directory stay unguarded for the same paths
  (the resolver is shared — reads must keep working for agent self-
  inspection; per architecture-recommendation.md C3).
- T9(a) — ``..``-style escapes and depth-2 vs depth-3 disambiguation.
- T9(b) — agent_mother.agent_modify returns the same refusal shape but
  with the smaller allowlist (memory.md only). R10 contract preserved.
- T11 — inner_soul _update_soul and _update_workflow refuse at the pre-
  write shim; memory/memories/user targets are unaffected.

**Deviation from T10's single-file naming**: tests/test_prompt_version_resolution.py
is owned by the parallel DB coder in this cycle. To avoid write-conflict on
a shared canonical file, this test module is filed under
``tests/test_prompt_artifact_guards.py`` and the DB coder adds their own
file. Both files cover distinct surfaces (DB resolution vs. write-side
guards).

**Fence discipline** (2026-10-12 critical note, zero tolerance): every test
uses ``tmp_path`` + ``monkeypatch.setattr`` on the cached agents-roots —
NEVER live-shaped paths. The ``agents_roots`` override is reset per-test via
the ``override_agents_roots`` fixture so a stale cache from one test cannot
leak into another.
"""

from __future__ import annotations

import pytest


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def override_agents_roots(tmp_path, monkeypatch):
    """Override the guard's agents-root candidate set to point at tmp_path.

    Without this fixture, the guard resolves to the real ``<repo>/agents/``
    directory (cached at module import). That works for the smoke checks at
    the bottom of every test file (using real-repo paths), but the per-test
    fixtures here MUST NOT touch live-shaped paths — fence discipline (2026-
    10-12 incident). The fixture:
      1. Clears the cached tuple.
      2. Creates ``<tmp_path>/agents/<test_agent>/...`` so containment has
         somewhere real to land.
      3. Sets the cached tuple to ``(tmp_path / "agents",)``.
      4. Restores both on teardown.
    """
    import daemon.tools.prompt_artifacts as pa

    pa.reset_agents_roots_cache()
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    # _baby_template: source for the agent_create template-copy path.
    baby = agents_root / "_baby_template"
    baby.mkdir()
    test_agent = agents_root / "test_agent"
    test_agent.mkdir()
    for agent_dir in (baby, test_agent):
        for fname in ("soul.md", "rule.md", "workflow.md", "tools_note.md",
                      "tools.md", "skill.md", "memory.md", "user.md", "growth.md"):
            (agent_dir / fname).write_text(f"# {fname}\n")

    monkeypatch.setattr(pa, "_DEFAULT_AGENTS_ROOTS_CACHE", (agents_root,))
    yield {
        "agents_root": agents_root,
        "test_agent": test_agent,
        "_baby_template": baby,
    }
    pa.reset_agents_roots_cache()


# ===========================================================================
# T9(a) — filesystem.write_file absolute + relative path refusal
# ===========================================================================


class TestWriteFileGuard:
    """write_file refuses writes against versioned-immutable artifacts."""

    def test_absolute_path_to_agents_dir_refused(
        self, override_agents_roots, tmp_path
    ):
        """Absolute path inside agents/<agent>/<versioned> → REFUSED."""
        from daemon.tools.filesystem import write_file

        target = override_agents_roots["test_agent"] / "soul.md"
        result = write_file.invoke({
            "content": "new content",
            "path": str(target),
        })

        assert "ERROR: REFUSED" in result
        assert "soul.md" in result
        assert "immutable prompt artifact" in result
        assert "prompt-version draft" in result
        assert "test_agent" in result
        assert "(file_path=" in result
        # The file must NOT have been mutated.
        assert target.read_text() == "# soul.md\n"

    def test_relative_path_inside_workdir_to_agents_dir_refused(
        self, override_agents_roots, tmp_path
    ):
        """Relative path inside workdir → resolved then refused."""
        from daemon.tools.filesystem import write_file

        # Construct a workdir that, combined with the relative path, lands
        # on the tmp agents-rooted soul.md. Use the tmp_path as workdir
        # (it contains the agents/ subtree the guard knows about).
        workdir = str(tmp_path)
        rel_path = "agents/test_agent/soul.md"

        result = write_file.invoke({
            "content": "x",
            "path": rel_path,
            "workdir": workdir,
        })

        assert "ERROR: REFUSED" in result
        assert "soul.md" in result

    def test_nested_workdir_dotdot_escape_attempt_refused(
        self, override_agents_roots, tmp_path
    ):
        """Nested workdir + ``../../agents/test_agent/soul.md`` → REFUSED.

        This is the R4 guard-bypass attempt: an agent nests workdir inside
        a sibling subtree and tries ``../../`` to climb out and reach the
        versioned artifact. The resolver's realpath containment must catch
        it.
        """
        from daemon.tools.filesystem import write_file

        # Create a nested workdir inside tmp_path so the relative path
        # resolves via the resolver (relative paths use workdir/relative
        # for the containment check). The ``../../`` then climbs out of
        # the nested workdir AND the workdir itself (2 levels) — but our
        # tmp_path IS the agent root's parent, so the resolved target
        # should land inside the override agents-root.
        nested = tmp_path / "subdir" / "leaf"
        nested.mkdir(parents=True)
        workdir = str(nested)
        rel_path = "../../agents/test_agent/soul.md"

        result = write_file.invoke({
            "content": "x",
            "path": rel_path,
            "workdir": workdir,
        })

        assert "ERROR: REFUSED" in result
        assert "soul.md" in result

    @pytest.mark.parametrize(
        "artifact",
        ["soul.md", "rule.md", "workflow.md", "tools_note.md",
         "tools.md", "skill.md"],
    )
    def test_each_versioned_artifact_refused(
        self, override_agents_roots, artifact
    ):
        """All six versioned-immutable artifacts must be refused."""
        from daemon.tools.filesystem import write_file

        target = override_agents_roots["test_agent"] / artifact
        result = write_file.invoke({
            "content": "x",
            "path": str(target),
        })

        assert "ERROR: REFUSED" in result
        assert artifact in result


# ===========================================================================
# T9(a) — filesystem.edit_file refusal (paired with write_file)
# ===========================================================================


class TestEditFileGuard:
    """edit_file refuses edits against versioned-immutable artifacts."""

    def test_absolute_path_to_agents_dir_refused(
        self, override_agents_roots
    ):
        from daemon.tools.filesystem import edit_file

        target = override_agents_roots["test_agent"] / "workflow.md"
        result = edit_file.invoke({
            "path": str(target),
            "old_string": "# workflow.md\n",
            "new_string": "# workflow.md\nchanged",
        })

        assert "ERROR: REFUSED" in result
        assert "workflow.md" in result
        assert target.read_text() == "# workflow.md\n"

    @pytest.mark.parametrize(
        "artifact",
        ["soul.md", "rule.md", "workflow.md", "tools_note.md",
         "tools.md", "skill.md"],
    )
    def test_each_versioned_artifact_refused_on_edit(
        self, override_agents_roots, artifact
    ):
        from daemon.tools.filesystem import edit_file

        target = override_agents_roots["test_agent"] / artifact
        result = edit_file.invoke({
            "path": str(target),
            "old_string": f"# {artifact}\n",
            "new_string": "tampered",
        })

        assert "ERROR: REFUSED" in result
        assert artifact in result


# ===========================================================================
# T9(a) — reads MUST stay allowed (the resolver is shared; C3 binding)
# ===========================================================================


class TestReadPathNotGuarded:
    """Reads against the same paths stay allowed (agent self-inspection).

    The guard is write-side only. Without this, an agent using read_file to
    verify its own soul.md (the typical self-inspection case) would see an
    ERROR and lose access to its own persona file.
    """

    def test_read_file_still_allowed_for_agents_dir(
        self, override_agents_roots
    ):
        from daemon.tools.filesystem import read_file

        target = override_agents_roots["test_agent"] / "soul.md"
        result = read_file.invoke({"path": str(target)})

        assert "ERROR: REFUSED" not in result
        assert "# soul.md" in result  # actual content returned

    def test_list_directory_still_allowed_for_agents_dir(
        self, override_agents_roots
    ):
        from daemon.tools.filesystem import list_directory

        agents_root = override_agents_roots["agents_root"]
        result = list_directory.invoke({"path": str(agents_root)})

        assert "ERROR: REFUSED" not in result
        assert "test_agent" in result  # the directory is listed


# ===========================================================================
# T9(a) — depth-2 vs depth-3 disambiguation
# ===========================================================================


class TestDepthDisambiguation:
    """Only depth-2 ``agents/<x>/<artifact>`` is refused.

    Depth-3 paths (e.g. ``agents/<x>/skills/soul.md``) and depth-1 paths
    (e.g. ``agents/notes/soul.md`` which is NOT an agent subtree) MUST
    pass the guard.
    """

    def test_depth_3_path_under_agent_skills_dir_allowed(
        self, override_agents_roots
    ):
        from daemon.tools.filesystem import write_file

        test_agent = override_agents_roots["test_agent"]
        # agent/<x>/skills/soul.md — depth-3; not a versioned artifact path
        skills_dir = test_agent / "skills"
        skills_dir.mkdir(exist_ok=True)
        target = skills_dir / "soul.md"

        result = write_file.invoke({
            "content": "x",
            "path": str(target),
        })

        assert "ERROR: REFUSED" not in result
        assert "SUCCESS" in result
        assert target.read_text() == "x"

    def test_agents_notes_md_at_depth_2_but_wrong_filename_allowed(
        self, override_agents_roots
    ):
        """Depth-2 path but non-versioned filename → allowed.

        ``agents/<x>/notes.md`` is a depth-2 path under the agents root
        but the filename ``notes.md`` is not in the versioned set, so the
        guard must pass it through.
        """
        from daemon.tools.filesystem import write_file

        test_agent = override_agents_roots["test_agent"]
        target = test_agent / "notes.md"

        result = write_file.invoke({
            "content": "x",
            "path": str(target),
        })

        assert "ERROR: REFUSED" not in result
        assert "SUCCESS" in result

    def test_underscore_agent_dirs_also_guarded(self, override_agents_roots):
        """``_``-prefixed agent dirs (``_baby_template``, ``_prompt_system``,
        ``_mother``) trigger the same depth-2 match — prevents template
        poisoning attacks where an attacker creates a ``_baby_template/
        soul.md`` to evade the guard.
        """
        from daemon.tools.filesystem import write_file

        agents_root = override_agents_roots["agents_root"]
        baby = agents_root / "_baby_template"
        baby.mkdir(exist_ok=True)
        target = baby / "soul.md"

        result = write_file.invoke({
            "content": "tamper",
            "path": str(target),
        })

        assert "ERROR: REFUSED" in result
        assert "_baby_template" in result


# ===========================================================================
# T9(b) — agent_mother.agent_modify write-path classification
#
# Phase B UPDATE (T8 rewire, 2026-10-14): the Phase A interim contract
# ("versioned artifacts REFUSED, allowlist = memory.md only") was
# superseded by the T8 DB-submission rewire — versioned artifacts now
# submit prompt-version drafts (pending_approval) and never touch the
# file; memory.md keeps the direct write; only truly non-modifiable
# files (growth.md, meta.json, ...) keep the refusal. The REAL wiring
# (pending rows, file-untouched, fail-closed, D9 invalidation) is
# pinned by ``tests/test_agent_modify_rewire.py``; the tests below
# pin the classification boundaries at the refusal edge.
# ===========================================================================


class TestAgentModifyAllowlistShrink:
    """T8 classification boundaries — refusal survives only for the
    non-versioned, non-memory residue."""

    def _tool(self, monkeypatch, tmp_path, service=None):
        """The REAL ``agent_modify`` closure against a tmp agents tree.

        Phase B T8 made the replay-style body-copy approach obsolete
        (the body now branches between DB submission and the memory
        carve-out), so these tests drive the real closure with a
        minimal manager stub — same pattern as
        ``tests/test_agent_modify_rewire.py``, which owns the
        exhaustive matrix.
        """
        import asyncio
        from pathlib import Path as _Path

        from daemon.tools.agent_mother import create_mother_tools

        agents_root = tmp_path / "agents"
        (agents_root / "developer").mkdir(parents=True, exist_ok=True)
        (agents_root / "developer" / "soul.md").write_text("OLD")
        (agents_root / "developer" / "memory.md").write_text("M0")
        monkeypatch.setattr(
            "daemon.tools.agent_mother.BASE_DIR", _Path(tmp_path)
        )

        class _PC:
            calls: list = []

            def invalidate(self, agent_id, version_tag=None):
                self.calls.append((agent_id, version_tag))

        class _Mgr:
            def __init__(self, svc):
                self._prompt_version_service = svc
                self.prompt_cache = _PC()

        mgr = _Mgr(service)
        tools = create_mother_tools(mgr, "inst-guard-test")
        tool = next(t for t in tools if t.name == "agent_modify")
        return tool, mgr

    def test_non_versioned_residue_still_refused(
        self, monkeypatch, tmp_path
    ):
        """Non-versioned residue (meta.json) keeps the Phase A refusal
        shape; ``growth.md`` joined the versioned set per User
        Directive #3 (2026-10-14, RESOLUTION-CORE) so it is no
        longer in this list.
        """
        for fname in ("meta.json",):
            tool, _ = self._tool(monkeypatch, tmp_path)
            result = asyncio_run_tool(
                tool, agent_name="developer", file=fname, content="x"
            )
            assert result["success"] is False
            assert f"Cannot modify '{fname}'" in result["error"]

    def test_versioned_artifact_submits_instead_of_refusing(
        self, monkeypatch, tmp_path
    ):
        """T8 supersession: soul.md → DB submission, NOT a refusal.

        Replaces the Phase A pin
        (``test_agent_modify_returns_refusal_for_versioned_artifacts``),
        which asserted the interim refusal that the T8 rewire was
        explicitly scheduled to replace. The full pending-row /
        file-untouched contract lives in
        ``tests/test_agent_modify_rewire.py``.
        """

        class _Row:
            version_id = "v-guard"
            version = 1
            state = "pending_approval"

        class _Svc:
            # async — mirrors the REAL service facade (A4/A5).
            async def create_and_submit(self, *a, **k):
                return _Row()

        tool, _ = self._tool(monkeypatch, tmp_path, service=_Svc())
        result = asyncio_run_tool(
            tool, agent_name="developer", file="soul.md", content="NEW"
        )
        assert result["success"] is True
        assert result["state"] == "pending_approval"
        # The file was NOT touched (the old refusal's purpose, kept).
        assert (tmp_path / "agents" / "developer" / "soul.md").read_text() == "OLD"

    def test_agent_modify_memory_md_now_routes_to_db(
        self, monkeypatch, tmp_path
    ):
        """memory.md joined the DB-versioned set (Ud#3 SUPERSEDES D6).

        Replaces the legacy ``test_agent_modify_memory_md_still_direct_write``
        which pinned the file-writer carve-out. The new regime:
        memory.md writes route through ``create_memory_version``
        (MEMORY-class, transition-free); the file on disk is
        UNTOUCHED.
        """

        class _Row:
            version_id = "v-mem"
            version = 1
            state = "approved"

        class _Svc:
            async def create_memory_version(self, *a, **k):
                return _Row()

        tool, _ = self._tool(monkeypatch, tmp_path, service=_Svc())
        result = asyncio_run_tool(
            tool, agent_name="developer", file="memory.md", content="M1"
        )

        assert result["success"] is True
        assert result["state"] == "approved"
        # File UNTOUCHED — the writer substrate is the DB row.
        assert (
            tmp_path / "agents" / "developer" / "memory.md"
        ).read_text() == "M0"

    def test_agent_modify_user_md_routes_to_memory_db(
        self, monkeypatch, tmp_path
    ):
        """user.md is MEMORY-class (Ud#3 SUPERSEDES D5).

        Replaces the prior test
        (``test_agent_modify_user_md_routes_to_db_submission``)
        which pinned the CORE submission path. The new regime:
        user.md writes route through ``create_memory_version``
        (MEMORY-class, transition-free, born-effective in
        ``state='approved'``). The file on disk is UNTOUCHED.
        """

        class _Row:
            version_id = "v-guard-u"
            version = 1
            state = "approved"

        class _Svc:
            async def create_memory_version(self, *a, **k):
                return _Row()

        tool, _ = self._tool(monkeypatch, tmp_path, service=_Svc())
        result = asyncio_run_tool(
            tool, agent_name="developer", file="user.md", content="U1"
        )
        assert result["success"] is True
        assert result["state"] == "approved"


def asyncio_run_tool(tool, **kwargs) -> dict:
    """Run an async langchain tool coroutine to completion (sync tests)."""
    import asyncio

    return asyncio.run(tool.coroutine(**kwargs))


# ===========================================================================
# T11 — inner_soul 2-site write shim
# ===========================================================================


class TestInnerSoulShim:
    """inner_soul write-surface disposition (Phase A T11 → Phase B T7).

    Phase A pinned the interim in-tool REFUSAL (the belt-and-braces
    shim refused soul/workflow touches while user.md still wrote).
    Phase B T7 replaces that regime: soul/workflow/user submit
    prompt-version DB drafts (D4/D5) and NEVER write files; the
    in-function shim is gone because there is no file write left to
    guard — the immutability fence lives on the ``write_file`` /
    ``edit_file`` tool surfaces (tested by the guard classes above)
    and on the fail-closed posture pinned here: with the prompt-version
    service unavailable, the change is REFUSED and the file stays
    byte-identical. No file fallback exists.
    """

    def test_soul_submission_fails_closed_without_service(self, override_agents_roots):
        """_update_soul refuses when no prompt-version service exists."""
        from daemon.tools.inner_soul import _update_soul

        test_agent = override_agents_roots["test_agent"]
        result = _update_soul(
            "test_agent", test_agent, "my name is Foo", {}, None
        )

        assert result["success"] is False
        assert result["target"] == "soul"
        assert "NOT applied" in result["error"]
        assert "immutable prompt artifact" in result["error"]
        # The file must NOT have been mutated (and no history/ dir).
        assert test_agent.joinpath("soul.md").read_text() == "# soul.md\n"
        assert not test_agent.joinpath("history").exists()

    def test_workflow_submission_fails_closed_without_service(self, override_agents_roots):
        """_update_workflow refuses when no prompt-version service exists."""
        from daemon.tools.inner_soul import _update_workflow

        test_agent = override_agents_roots["test_agent"]
        result = _update_workflow(
            "test_agent", test_agent, "new step", {}, None
        )

        assert result["success"] is False
        assert result["target"] == "workflow"
        assert "NOT applied" in result["error"]
        assert test_agent.joinpath("workflow.md").read_text() == "# workflow.md\n"

    def test_user_submission_fails_closed_without_service(self, override_agents_roots):
        """_update_user refuses without a service (D5 — no file fallback).

        Phase A pinned ``user.md still writes``; Phase B D5 flips the
        disposition: user.md joins the DB version model, so without the
        service the change is refused and the file stays untouched.
        """
        from daemon.tools.inner_soul import _update_user

        test_agent = override_agents_roots["test_agent"]
        result = _update_user(
            "test_agent", test_agent, "my name is bar", None
        )

        assert result["success"] is False
        assert result["target"] == "user"
        assert "NOT applied" in result["error"]
        # If a user.md template exists, it is byte-identical to before
        # (no file fallback, no partial write).
        user_file = test_agent.joinpath("user.md")
        if user_file.exists():
            content = user_file.read_text()
            assert "my name is bar" not in content

    def test_memory_target_fails_closed_without_service(
        self, override_agents_roots
    ):
        """_update_memory_md fails closed without a service (Ud#3).

        Replaces the legacy ``test_memory_target_unaffected`` which
        pinned the file-writer carve-out. The new regime: memory.md
        is MEMORY-class; without the prompt-version service the
        change is refused and the file stays untouched.
        """
        from daemon.tools.inner_soul import _update_memory_md

        test_agent = override_agents_roots["test_agent"]
        result = _update_memory_md(
            "test_agent", test_agent, "some new memory", {}, None,
            instance_id=None,
        )

        # The MEMORY creator is fail-closed — no service means no
        # persist; the file is the cold-boot F6 fallback (NOT the
        # writer).
        assert result.get("success") is False
        assert result.get("target") == "memory"
        assert "NOT applied" in result.get("error", "")
        # File UNTOUCHED.
        assert "some new memory" not in test_agent.joinpath(
            "memory.md"
        ).read_text()

    # NOTE (User Directive #3, 2026-10-14, RESOLUTION-CORE): the
    # former ``test_user_target_unaffected`` (``user.md still
    # writes``) is SUPERSEDED — user.md joins the MEMORY-class set;
    # see ``test_user_submission_fails_closed_without_service``
    # above for the closed-loop fail-closed pin.

    def test_memories_target_fails_closed_without_service(
        self, override_agents_roots
    ):
        """_update_memories fails closed without a service (Ud#3).

        Replaces the legacy ``test_memories_target_unaffected`` which
        pinned the file-writer carve-out. The new regime: per-slug
        MEMORY-class row; no service means no persist; no file
        under memories/ is written.
        """
        from daemon.tools.inner_soul import _update_memories

        test_agent = override_agents_roots["test_agent"]
        result = _update_memories(
            "test_agent",
            test_agent,
            "observation",
            {"category": "event", "type": "event", "description": "test event"},
            None,
            instance_id=None,
        )

        # MEMORY creator is fail-closed — no service means no
        # persist; no file is written.
        assert result.get("success") is False
        # No file under memories/ written.
        memories_files = list(
            (test_agent / "memories").glob("*.md")
        )
        assert memories_files == []


# ===========================================================================
# Guard module direct API tests — pin VERSIONED_PROMPT_ARTIFACTS shape
# ===========================================================================


class TestGuardModuleAPI:
    """Pin the public API of ``daemon.tools.prompt_artifacts``."""

    def test_versioned_prompt_artifacts_is_frozenset(self):
        from daemon.tools.prompt_artifacts import VERSIONED_PROMPT_ARTIFACTS

        assert isinstance(VERSIONED_PROMPT_ARTIFACTS, frozenset)

    def test_versioned_prompt_artifacts_exact_contents(self):
        """Exact pin — any drift in the artifact list is a contract change.

        User Directive #3 (2026-10-14, RESOLUTION-CORE) added
        ``memory.md`` and ``growth.md`` to the versioned-immutable
        set. The exact list is the contract.
        """
        from daemon.tools.prompt_artifacts import VERSIONED_PROMPT_ARTIFACTS

        assert VERSIONED_PROMPT_ARTIFACTS == frozenset({
            "soul.md",
            "rule.md",
            "workflow.md",
            "tools_note.md",
            "tools.md",  # legacy fallback
            "skill.md",  # legacy single-skill loader
            # User Directive #3, 2026-10-14, RESOLUTION-CORE
            "memory.md",
            "growth.md",
        })

    def test_check_write_returns_none_for_unrelated_path(
        self, override_agents_roots
    ):
        from daemon.tools.prompt_artifacts import check_write
        from pathlib import Path

        # A path completely outside any agents-root → None (allowed).
        unrelated = Path("/tmp/some_other_path.md")
        assert check_write(unrelated) is None

    def test_check_write_returns_typed_refusal(
        self, override_agents_roots
    ):
        from daemon.tools.prompt_artifacts import check_write
        from pathlib import Path

        target = (
            override_agents_roots["test_agent"] / "soul.md"
        ).resolve()
        result = check_write(target)

        assert result is not None
        assert result.startswith("ERROR: REFUSED")
        assert "soul.md" in result
        assert "immutable prompt artifact" in result
        assert "submit a prompt-version draft" in result
        assert "(file_path=" in result
        assert "test_agent" in result


# ===========================================================================
# Preserved-exception tests — agent_create / agent_delete bypass the guard
# ===========================================================================


class TestPreservedExceptions:
    """Phase A preserved-exception audit (T9 acceptance: agent_create /
    agent_delete stay legal). Both paths use ``shutil.copy2`` /
    ``shutil.move`` and write directly — they bypass ``filesystem.write_file``
    entirely, so the guard has no surface to fire on.

    These tests are the structural pin: invoking the underlying shutil
    primitives against a versioned artifact path succeeds (no guard
    interference), and the resulting on-disk state is correct.
    """

    def test_agent_create_template_copy_bypasses_guard(
        self, override_agents_roots, monkeypatch
    ):
        """``shutil.copy2`` from ``_baby_template/<artifact>`` to a new
        agent dir succeeds — the preserved-exception path."""
        import shutil

        agents_root = override_agents_roots["agents_root"]
        template_dir = override_agents_roots["_baby_template"]
        new_agent_dir = agents_root / "new_agent"
        new_agent_dir.mkdir()

        # Copy each versioned artifact file from template to new agent.
        for artifact in ("soul.md", "rule.md", "workflow.md"):
            src = template_dir / artifact
            dst = new_agent_dir / artifact
            shutil.copy2(src, dst)  # NOT filesystem.write_file

            assert dst.exists()
            assert dst.read_text() == f"# {artifact}\n"

    def test_agent_delete_move_to_trash_bypasses_guard(
        self, override_agents_roots
    ):
        """``shutil.move`` to ``_trash/`` succeeds — the preserved-exception
        path. The versioned artifact files travel with the agent dir."""
        import shutil

        agents_root = override_agents_roots["agents_root"]
        test_agent = agents_root / "test_agent"
        trash_dir = agents_root / "_trash"
        trash_dir.mkdir()

        # shutil.move the WHOLE agent dir — the artifacts move along.
        moved = shutil.move(str(test_agent), str(trash_dir / "test_agent_moved"))

        # The versioned artifacts moved with the agent dir.
        moved_path = __import__("pathlib").Path(moved)
        assert moved_path.joinpath("soul.md").exists()
        assert moved_path.joinpath("workflow.md").exists()
        assert moved_path.joinpath("rule.md").exists()