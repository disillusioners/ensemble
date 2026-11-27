"""F9 — read-surface honesty + scoped-refusal journal logging.

Covers the seven accepted sub-items of the skill-evolution R1 front-door fix:

(a) anchor + commit disclosure on walk-tool results,
(b) walk caps visible even when no cap fired,
(c) ``grep_files(fixed_string=True)`` (literal mode, composed with
    ``whole_word`` / ``case_sensitive``; regex mode unchanged),
(d) explicit tool-specific FILE-as-path refusals,
(e) WARNING journal lines for scoped refusals and the silent candidate drop.

Hermetic: tmp_path trees only; ``git init`` here is a TEST fixture, not a
daemon tool-layer subprocess. No DB, no network, no live install paths.

F-1 follow-up (honesty of the UNKNOWN REASON): ``_git_commit_of_tree``
returns ``UNKNOWN (not a git checkout)`` only when the anchor really
carries NO ``.git`` marker. Every other failure mode — an unborn HEAD
from a fresh ``git init``, a broken ``gitdir:`` link, a garbage loose
ref, an unreadable HEAD — is a CHECKOUT whose commit could not be
resolved, and each must say so.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from daemon.tools import filesystem as fs


CANARY = "F9_CANARY_8d41c2"


@pytest.fixture(autouse=True)
def _clear_scope_cache():
    fs._READ_SCOPE_CACHE.clear()
    yield
    fs._READ_SCOPE_CACHE.clear()


@pytest.fixture
def scope_env(monkeypatch, tmp_path):
    """A hermetic declared scope: agent meta under a tmp agents root.

    Returns a context manager binding (agent id, trusted anchor, tool name)
    into the same contextvars the production factory binds.
    """
    anchor = tmp_path / "anchor"
    (anchor / "agents" / "skill-keeper").mkdir(parents=True)
    (anchor / "agents" / "skill-keeper" / "meta.json").write_text(
        json.dumps({"id": "skill-keeper", "read_scope": "project"}),
        encoding="utf-8",
    )
    from daemon.tools import prompt_artifacts

    monkeypatch.setattr(
        prompt_artifacts, "get_agents_roots", lambda: (anchor / "agents",)
    )

    @contextmanager
    def _bind(tool: str = "glob_files", agent: str = "skill-keeper"):
        tokens = (
            fs.set_trusted_workdir(str(anchor)),
            fs.set_caller_agent_id(agent),
            fs.set_current_read_tool_name(tool),
        )
        try:
            yield anchor
        finally:
            fs.reset_current_read_tool_name(tokens[2])
            fs.reset_caller_agent_id(tokens[1])
            fs.reset_trusted_workdir(tokens[0])

    return _bind


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _make_git_repo(path: Path) -> str:
    """A throwaway commit; return its full HEAD sha."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "f9@example.invalid", cwd=path)
    _git("config", "user.name", "F9 Test", cwd=path)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=path)
    _git("commit", "-qm", "f9 fixture", cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


def _git_available() -> bool:
    return shutil.which("git") is not None


# =============================================================================
# (a) anchor + commit disclosure
# =============================================================================


class TestAnchorCommitDisclosure:
    def test_no_match_states_root_anchor_and_unknown_commit(self, scope_env):
        with scope_env("glob_files") as anchor:
            (anchor / "inside.txt").write_text("inside\n", encoding="utf-8")
            result = fs.glob_files.invoke({"pattern": "*.py", "path": "."})
        assert "No files matching" in result  # pre-existing pin preserved
        assert fs.READ_SURFACE_MARKER in result
        assert f"search root: {anchor}" in result
        assert f"anchor: {anchor}" in result
        assert "commit: UNKNOWN (not a git checkout)" in result
        assert "⚠ Search incomplete" not in result

    def test_grep_no_match_states_root_anchor_and_commit(self, scope_env):
        with scope_env("grep_files") as anchor:
            result = fs.grep_files.invoke(
                {"pattern": "definitely-absent", "path": "."}
            )
        assert "No matches found for: definitely-absent" in result
        assert "--- [read surface]" in result
        assert f"search root: {anchor}" in result
        assert f"anchor: {anchor}" in result
        assert "commit:" in result

    def test_successful_result_also_states_the_resolved_root(self, scope_env):
        with scope_env("glob_files") as anchor:
            (anchor / "inside.txt").write_text("inside\n", encoding="utf-8")
            result = fs.glob_files.invoke({"pattern": "*.txt", "path": "."})
        assert "inside.txt" in result
        assert f"search root: {anchor}" in result
        assert fs.READ_SURFACE_MARKER in result

    def test_non_git_anchor_reports_unknown_explicitly(self, tmp_path):
        assert fs._git_commit_of_tree(tmp_path) == "UNKNOWN (not a git checkout)"
        assert fs._git_commit_of_tree(None) == "UNKNOWN (no trusted anchor)"

    @pytest.mark.skipif(not _git_available(), reason="git binary unavailable")
    def test_real_git_checkout_anchor_reports_its_own_head_sha(self, scope_env, tmp_path):
        repo = tmp_path / "real-repo"
        sha = _make_git_repo(repo)
        with scope_env("glob_files") as anchor:
            tokens = (
                fs.set_trusted_workdir(str(repo)),
                fs.set_caller_agent_id("skill-keeper"),
            )
            try:
                result = fs.glob_files.invoke({"pattern": "*.py", "path": "."})
            finally:
                fs.reset_caller_agent_id(tokens[1])
                fs.reset_trusted_workdir(tokens[0])
        assert f"commit: {sha[:12]} (12-char short HEAD sha)" in result
        assert f"anchor: {repo}" in result

    @pytest.mark.skipif(not _git_available(), reason="git binary unavailable")
    def test_worktree_gitdir_file_form_resolves_commit(self, tmp_path):
        """The canonical anchor shape: `.git` is a FILE pointing at gitdir."""
        repo = tmp_path / "repo"
        sha = _make_git_repo(repo)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        # Relative gitdir path — resolved against the ANCHOR dir.
        (worktree / ".git").write_text(
            f"gitdir: {repo / '.git'}\n", encoding="utf-8"
        )
        assert fs._git_commit_of_tree(worktree) == (
            f"{sha[:12]} (12-char short HEAD sha)"
        )

        # HEAD as a branch ref: loose ref in the linked gitdir first.
        linked = tmp_path / "linked-gitdir"
        linked.mkdir()
        (linked / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
        (linked / "refs" / "heads").mkdir(parents=True)
        (linked / "refs" / "heads" / "topic").write_text(sha + "\n", encoding="utf-8")
        # Relative gitdir is resolved against the ANCHOR, not tmp_path.
        (worktree / ".git").write_text("gitdir: ../linked-gitdir\n", encoding="utf-8")
        assert fs._git_commit_of_tree(worktree) == (
            f"{sha[:12]} (12-char short HEAD sha)"
        )

    @pytest.mark.skipif(not _git_available(), reason="git binary unavailable")
    def test_packed_refs_fallback_resolves_commit(self, tmp_path):
        repo = tmp_path / "repo"
        sha = _make_git_repo(repo)
        git_dir = repo / ".git"
        ref = (git_dir / "HEAD").read_text(encoding="utf-8").strip()[5:]
        (git_dir / ref).unlink()
        (git_dir / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled sorted \n{sha} {ref}\n",
            encoding="utf-8",
        )
        assert fs._git_commit_of_tree(repo) == (
            f"{sha[:12]} (12-char short HEAD sha)"
        )


# =============================================================================
# (f) F-1 — the UNKNOWN reason must stay TRUE about the anchor
# =============================================================================


class TestUnknownCommitReasonDifferentiation:
    """``UNKNOWN (not a git checkout)`` is a STATEMENT about the anchor.

    It is only true when the anchor carries NO ``.git`` marker at all.
    A fresh ``git init`` (unborn HEAD), a worktree ``.git`` file whose
    ``gitdir:`` target is missing, a loose ref holding garbage and an
    unreadable HEAD are all CHECKOUTS whose commit could not be
    resolved — reporting them as "not a git checkout" tells a scoped
    agent something factually false about its own tree.
    """

    @pytest.mark.skipif(not _git_available(), reason="git binary unavailable")
    def test_unborn_head_reports_no_commit_resolved(self, tmp_path):
        """A fresh ``git init`` with ZERO commits IS a git checkout."""
        repo = tmp_path / "unborn"
        repo.mkdir()
        _git("init", "-q", cwd=repo)
        assert (repo / ".git").is_dir()  # it IS a checkout
        result = fs._git_commit_of_tree(repo)
        assert result == "UNKNOWN (no commit resolved)"
        assert "not a git checkout" not in result

    def test_broken_gitdir_link_is_not_reported_as_not_a_checkout(self, tmp_path):
        """A worktree ``.git`` FILE whose ``gitdir:`` target does not exist."""
        worktree = tmp_path / "broken-link"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: ../missing-gitdir\n", encoding="utf-8")
        result = fs._git_commit_of_tree(worktree)
        assert result == "UNKNOWN (git metadata unreadable)"
        assert "not a git checkout" not in result

    def test_malformed_git_file_is_not_reported_as_not_a_checkout(self, tmp_path):
        """A ``.git`` FILE that is not a ``gitdir:`` link at all."""
        worktree = tmp_path / "malformed-link"
        worktree.mkdir()
        (worktree / ".git").write_text("garbage\n", encoding="utf-8")
        result = fs._git_commit_of_tree(worktree)
        assert result == "UNKNOWN (git metadata unreadable)"
        assert "not a git checkout" not in result

    def test_unresolvable_gitdir_target_reports_metadata_unreadable(self, tmp_path):
        """The ``.git`` PROBE itself raises (gitdir target cannot resolve)."""
        worktree = tmp_path / "unresolvable-gitdir"
        worktree.mkdir()
        # An embedded NUL makes the gitdir target unresolvable ⇒ the
        # ``gitdir:`` probe raises ValueError inside the metadata probe.
        (worktree / ".git").write_text(
            "gitdir: /tmp/\x00f9-missing-gitdir\n", encoding="utf-8"
        )
        result = fs._git_commit_of_tree(worktree)
        assert result == "UNKNOWN (git metadata unreadable)"
        assert "not a git checkout" not in result

    def test_garbage_loose_ref_reports_no_commit_resolved(self, tmp_path):
        """HEAD names a ref whose loose file holds a non-sha."""
        repo = tmp_path / "garbage-ref"
        (repo / ".git" / "refs" / "heads").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/x\n", encoding="utf-8")
        (repo / ".git" / "refs" / "heads" / "x").write_text("not-a-sha\n", encoding="utf-8")
        result = fs._git_commit_of_tree(repo)
        assert result == "UNKNOWN (no commit resolved)"
        assert "not a git checkout" not in result

    def test_unreadable_head_reports_head_unreadable(self, tmp_path):
        """The git dir is found but its HEAD file cannot be read."""
        repo = tmp_path / "head-unreadable"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").mkdir()  # a DIRECTORY ⇒ read_text raises OSError
        result = fs._git_commit_of_tree(repo)
        assert result == "UNKNOWN (HEAD unreadable)"
        assert "not a git checkout" not in result

    def test_unresolvable_anchor_path_reports_anchor_path_unresolvable(self):
        """The ANCHOR PATH itself cannot be resolved (embedded NUL byte)."""
        result = fs._git_commit_of_tree("/tmp/\x00f9-unresolvable-anchor")
        assert result == "UNKNOWN (anchor path unresolvable)"
        assert "not a git checkout" not in result

    def test_non_git_dir_still_reports_not_a_git_checkout(self, tmp_path):
        """The TRUE negative survives: no ``.git`` marker at all."""
        plain = tmp_path / "plain"
        plain.mkdir()
        assert fs._git_commit_of_tree(plain) == "UNKNOWN (not a git checkout)"

    @pytest.mark.skipif(not _git_available(), reason="git binary unavailable")
    def test_distinct_failure_modes_carry_distinct_reasons(self, tmp_path):
        """No two different failure modes collapse onto one reason."""
        unborn = tmp_path / "unborn"
        unborn.mkdir()
        _git("init", "-q", cwd=unborn)

        broken = tmp_path / "broken-link"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: ../missing-gitdir\n", encoding="utf-8")

        garbage = tmp_path / "garbage-ref"
        (garbage / ".git" / "refs" / "heads").mkdir(parents=True)
        (garbage / ".git" / "HEAD").write_text("ref: refs/heads/x\n", encoding="utf-8")
        (garbage / ".git" / "refs" / "heads" / "x").write_text("not-a-sha\n", encoding="utf-8")

        unreadable = tmp_path / "head-unreadable"
        (unreadable / ".git").mkdir(parents=True)
        (unreadable / ".git" / "HEAD").mkdir()

        plain = tmp_path / "plain"
        plain.mkdir()

        results = {
            "unborn": fs._git_commit_of_tree(unborn),
            "broken_gitdir": fs._git_commit_of_tree(broken),
            "garbage_ref": fs._git_commit_of_tree(garbage),
            "unreadable_head": fs._git_commit_of_tree(unreadable),
            "plain": fs._git_commit_of_tree(plain),
        }
        assert results["unborn"] == "UNKNOWN (no commit resolved)"
        assert results["broken_gitdir"] == "UNKNOWN (git metadata unreadable)"
        assert results["garbage_ref"] == "UNKNOWN (no commit resolved)"
        assert results["unreadable_head"] == "UNKNOWN (HEAD unreadable)"
        assert results["plain"] == "UNKNOWN (not a git checkout)"
        # "not a git checkout" is reserved for the ONE truly-non-checkout
        # fixture — it must not leak onto any of the other four.
        leaks = [
            name
            for name, reason in results.items()
            if reason == "UNKNOWN (not a git checkout)" and name != "plain"
        ]
        assert leaks == [], results


# =============================================================================
# (b) caps visible without firing
# =============================================================================


class TestCapsVisible:
    def test_cap_value_visible_on_happy_path_without_banner(self, tmp_path):
        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "file-count=10,000" in result
        assert "depth=10" in result
        assert "matches=10,000" in result
        assert "⚠ Search incomplete" not in result

    def test_cap_fired_banner_is_distinct_from_footer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fs, "WALK_MAX_FILE_COUNT", 2)
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("x\n", encoding="utf-8")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "⚠ Search incomplete" in result
        assert "file-count cap reached (2 files)" in result
        assert fs.READ_SURFACE_MARKER in result
        assert "file-count=2" in result  # footer reads the live constant


# =============================================================================
# (c) fixed_string (grep_files only)
# =============================================================================


class TestFixedString:
    @pytest.fixture
    def text_tree(self, tmp_path):
        (tmp_path / "sample.txt").write_text(
            "literal a.b here\nregex axb here\n", encoding="utf-8"
        )
        return tmp_path

    def test_fixed_string_matches_metacharacters_literally(self, text_tree):
        out = fs.grep_files.invoke(
            {"pattern": "a.b", "path": str(text_tree), "fixed_string": True}
        )
        assert "literal a.b here" in out
        assert "regex axb here" not in out

    def test_regex_mode_unchanged_when_fixed_string_false(self, text_tree):
        out = fs.grep_files.invoke({"pattern": "a.b", "path": str(text_tree)})
        assert "regex axb here" in out

    def test_fixed_string_composes_with_whole_word(self, tmp_path):
        (tmp_path / "w.txt").write_text(
            "only a.b here\nxa.bx here\na.bx here\n", encoding="utf-8"
        )
        out = fs.grep_files.invoke(
            {
                "pattern": "a.b",
                "path": str(tmp_path),
                "fixed_string": True,
                "whole_word": True,
            }
        )
        assert "only a.b here" in out
        assert "xa.bx here" not in out
        assert "a.bx here" not in out

    def test_fixed_string_composes_with_case_sensitive(self, tmp_path):
        (tmp_path / "c.txt").write_text("TODO item\ntodo item\n", encoding="utf-8")
        out = fs.grep_files.invoke(
            {
                "pattern": "TODO",
                "path": str(tmp_path),
                "fixed_string": True,
                "case_sensitive": True,
            }
        )
        assert "TODO item" in out
        assert "todo item" not in out
        relaxed = fs.grep_files.invoke(
            {"pattern": "TODO", "path": str(tmp_path), "fixed_string": True}
        )
        assert "todo item" in relaxed

    def test_invalid_regex_error_unchanged(self, tmp_path):
        out = fs.grep_files.invoke({"pattern": "[", "path": str(tmp_path)})
        assert out.startswith("ERROR: Invalid regex pattern:")

    def test_fixed_string_is_grep_only(self):
        # StructuredTool exposes the agent-visible parameter names via .args.
        assert "fixed_string" not in fs.glob_files.args
        assert "fixed_string" in fs.grep_files.args


# =============================================================================
# (d) FILE-as-path refusals
# =============================================================================


class TestFileAsPathRefusal:
    GLOB_TEXT = (
        "ERROR: path is a FILE, not a directory — pass the containing "
        "directory as path and put the glob in pattern."
    )
    GREP_TEXT = (
        "ERROR: path is a FILE, not a directory — pass the containing "
        "directory as path and use include=<file basename>."
    )

    def test_glob_exact_refusal(self, tmp_path):
        target = tmp_path / "scratch.txt"
        target.write_text("hello\n", encoding="utf-8")
        result = fs.glob_files.invoke(
            {"pattern": "*.txt", "path": str(target), "workdir": str(tmp_path)}
        )
        assert result == self.GLOB_TEXT

    def test_grep_exact_refusal(self, tmp_path):
        target = tmp_path / "scratch.txt"
        target.write_text("hello\n", encoding="utf-8")
        result = fs.grep_files.invoke(
            {"pattern": "hello", "path": str(target), "workdir": str(tmp_path)}
        )
        assert result == self.GREP_TEXT

    def test_scoped_file_as_path_is_still_refused_without_content(self, scope_env):
        with scope_env("grep_files") as anchor:
            target = anchor / "scratch.txt"
            target.write_text(f"hello {CANARY}\n", encoding="utf-8")
            result = fs.grep_files.invoke({"pattern": "hello", "path": "scratch.txt"})
        assert result == self.GREP_TEXT
        assert CANARY not in result


# =============================================================================
# (e) scoped-refusal journal logging
# =============================================================================


def _records(caplog, event: str):
    return [r for r in caplog.records if getattr(r, "event", None) == event]


class TestRefusalLogging:
    def test_instance_scope_refusal_logs_once_at_warning(self, scope_env, caplog):
        """The live-observed 'workdir argument' refusal, via the real factory."""
        from daemon.tools.instance import create_instance_tools

        with scope_env("grep_files") as anchor:
            manager = MagicMock()
            manager.config = SimpleNamespace(llm=SimpleNamespace(allowed_models=[]))
            meta = MagicMock()
            meta.project_id = "proj-f9"
            manager._instance_repository.get = MagicMock(return_value=meta)
            project = MagicMock()
            project.main_directory = str(anchor)
            manager._project_repository.get = MagicMock(return_value=project)
            tools = {
                getattr(t, "name", None): t
                for t in create_instance_tools(manager, "inst-f9", agent_id="skill-keeper")
            }
            caplog.set_level(logging.WARNING, logger="daemon.tools.instance")
            result = tools["grep_files"].invoke(
                {"pattern": "x", "path": ".", "workdir": "/"}
            )

        assert result.startswith("ERROR: read scope")
        assert "fixes the read anchor" in result
        records = _records(caplog, fs.READ_SCOPE_REFUSAL_EVENT)
        assert len(records) == 1, f"expected ONE refusal log line: {caplog.text}"
        rec = records[0]
        assert rec.levelno == logging.WARNING
        assert fs.READ_SCOPE_REFUSAL_TOKEN in rec.getMessage()
        assert "agent=skill-keeper" in rec.getMessage()
        assert "tool=grep_files" in rec.getMessage()
        assert "reason=agent-supplied-workdir" in rec.getMessage()

    def test_filesystem_scope_refusal_logs_once_at_warning(self, scope_env, caplog):
        caplog.set_level(logging.WARNING, logger="daemon.tools.filesystem")
        with scope_env("read_file") as anchor:
            result = fs.read_file.invoke(
                {"path": str(anchor / "outside.txt"), "workdir": str(anchor)}
            )
        assert result.startswith("ERROR: read scope")
        records = _records(caplog, fs.READ_SCOPE_REFUSAL_EVENT)
        assert len(records) == 1, f"expected ONE refusal log line: {caplog.text}"
        rec = records[0]
        assert rec.levelno == logging.WARNING
        assert fs.READ_SCOPE_REFUSAL_TOKEN in rec.getMessage()
        assert "agent=skill-keeper" in rec.getMessage()
        assert "tool=read_file" in rec.getMessage()
        assert "reason=" in rec.getMessage()

    def test_silent_candidate_drop_logged_once_with_count(self, scope_env, caplog, tmp_path):
        caplog.set_level(logging.WARNING, logger="daemon.tools.filesystem")
        with scope_env("glob_files") as anchor:
            (anchor / "ok.txt").write_text("fine\n", encoding="utf-8")
            (anchor / ".env").write_text(f"SECRET={CANARY}\n", encoding="utf-8")
            (anchor / ".env.local").write_text(f"SECRET2={CANARY}\n", encoding="utf-8")
            outside = tmp_path / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            try:
                (anchor / "link.txt").symlink_to(outside)
            except (OSError, NotImplementedError):
                pytest.skip("symlinks unavailable on this host")
            result = fs.glob_files.invoke({"pattern": "*", "path": "."})

        assert "ok.txt" in result
        assert ".env" not in [line.strip() for line in result.splitlines()]
        records = _records(caplog, fs.READ_SCOPE_DROP_EVENT)
        assert len(records) == 1, f"expected ONE drop log line: {caplog.text}"
        rec = records[0]
        assert rec.levelno == logging.WARNING
        assert fs.READ_SCOPE_DROP_TOKEN in rec.getMessage()
        assert "dropped=3" in rec.getMessage()
        assert "agent=skill-keeper" in rec.getMessage()
        assert "tool=glob_files" in rec.getMessage()
        assert CANARY not in rec.getMessage(), "log must never carry content"

    def test_no_drop_log_when_nothing_dropped(self, scope_env, caplog):
        caplog.set_level(logging.INFO, logger="daemon.tools.filesystem")
        with scope_env("glob_files") as anchor:
            (anchor / "ok.txt").write_text("fine\n", encoding="utf-8")
            fs.glob_files.invoke({"pattern": "*", "path": "."})
        assert _records(caplog, fs.READ_SCOPE_DROP_EVENT) == []

    def test_operator_count_regex_matches_both_refusals_not_the_drop(
        self, scope_env, caplog
    ):
        """The mandated journalctl grep counts BOTH refusals, never the drop.

        Proven gap (2026-11-26): the operator's
        ``grep -cE ' - daemon\\.tools\\.(filesystem|instance) - (INFO|WARNING) - .*read scope'``
        matched only 1 of 3 lines because the lowercase phrase ``read scope``
        lived inside the filesystem refusal's ``reason=`` text by accident.
        The phrase must be a property of the MESSAGE, so the live-observed
        instance.py workdir refusal is counted too, while the candidate drop
        (which has its own token + command) stays out of the refusal count.
        """
        import re

        from daemon.tools.instance import create_instance_tools

        operator_re = re.compile(
            r" - daemon\.tools\.(filesystem|instance) - (INFO|WARNING) - .*read scope"
        )
        caplog.set_level(logging.WARNING, logger="daemon.tools.instance")
        caplog.set_level(logging.WARNING, logger="daemon.tools.filesystem")

        def _journal_line(rec):
            # Same shape as `journalctl -o cat`: " - <logger> - <LEVEL> - <msg>".
            return (
                f" - {rec.name} - {logging.getLevelName(rec.levelno)} - "
                f"{rec.getMessage()}"
            )

        # (i) the LIVE-OBSERVED instance.py agent-supplied-workdir refusal.
        with scope_env("grep_files") as anchor:
            manager = MagicMock()
            manager.config = SimpleNamespace(llm=SimpleNamespace(allowed_models=[]))
            meta = MagicMock()
            meta.project_id = "proj-f9"
            manager._instance_repository.get = MagicMock(return_value=meta)
            project = MagicMock()
            project.main_directory = str(anchor)
            manager._project_repository.get = MagicMock(return_value=project)
            tools = {
                getattr(t, "name", None): t
                for t in create_instance_tools(
                    manager, "inst-f9", agent_id="skill-keeper"
                )
            }
            inst_result = tools["grep_files"].invoke(
                {"pattern": "x", "path": ".", "workdir": "/"}
            )
        assert inst_result.startswith("ERROR: read scope")

        # (ii) the filesystem.py scoped-refusal.
        with scope_env("read_file") as anchor:
            fs_result = fs.read_file.invoke(
                {"path": str(anchor / "outside.txt"), "workdir": str(anchor)}
            )
        assert fs_result.startswith("ERROR: read scope")

        # (iii) the candidate drop — denied basename, no symlink needed.
        with scope_env("glob_files") as anchor:
            (anchor / "ok.txt").write_text("fine\n", encoding="utf-8")
            (anchor / ".env").write_text(f"SECRET={CANARY}\n", encoding="utf-8")
            drop_result = fs.glob_files.invoke({"pattern": "*", "path": "."})
        assert "ok.txt" in drop_result

        inst_lines = [
            _journal_line(r)
            for r in _records(caplog, fs.READ_SCOPE_REFUSAL_EVENT)
            if r.name == "daemon.tools.instance"
        ]
        fs_lines = [
            _journal_line(r)
            for r in _records(caplog, fs.READ_SCOPE_REFUSAL_EVENT)
            if r.name == "daemon.tools.filesystem"
        ]
        drop_lines = [
            _journal_line(r) for r in _records(caplog, fs.READ_SCOPE_DROP_EVENT)
        ]

        assert len(inst_lines) == 1, f"instance refusal lines: {inst_lines}"
        assert len(fs_lines) == 1, f"filesystem refusal lines: {fs_lines}"
        assert len(drop_lines) == 1, f"candidate drop lines: {drop_lines}"

        # BOTH refusals match the operator's count regex; the drop does NOT.
        assert operator_re.search(inst_lines[0]), inst_lines[0]
        assert operator_re.search(fs_lines[0]), fs_lines[0]
        assert not operator_re.search(drop_lines[0]), drop_lines[0]

        # Every refusal line carries the stable ALL-CAPS token.
        assert fs.READ_SCOPE_REFUSAL_TOKEN in inst_lines[0]
        assert fs.READ_SCOPE_REFUSAL_TOKEN in fs_lines[0]

    def test_level_constant_is_single_warning_definition(self):
        """ONE named constant, == WARNING, used at all three log sites."""
        import inspect
        import re

        from daemon.tools import instance as inst

        assert fs.SCOPE_REFUSAL_LOG_LEVEL == logging.WARNING

        # The level is passed via the constant at every site (never a literal
        # INFO/WARNING at a call site) and no bare logger.info() survives.
        uses_constant = re.compile(r"logger\.log\(\s*SCOPE_REFUSAL_LOG_LEVEL,")

        # filesystem.py: both sites use the constant, no bare logger.info.
        fs_refusal_src = inspect.getsource(fs._log_scope_refusal)
        assert uses_constant.search(fs_refusal_src)
        assert "logger.info(" not in fs_refusal_src
        fs_drop_src = inspect.getsource(fs._scope_filter_candidates)
        assert uses_constant.search(fs_drop_src)
        assert "logger.info(" not in fs_drop_src

        # instance.py: imports the SAME constant from filesystem, uses it at
        # its one refusal site, with no hardcoded INFO/literal level there.
        inst_factory_src = inspect.getsource(inst._make_read_scope_aware)
        assert "SCOPE_REFUSAL_LOG_LEVEL," in inst_factory_src
        assert uses_constant.search(inst_factory_src)
        assert "logger.info(" not in inst_factory_src
