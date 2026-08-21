"""End-to-end HTTP tests for the Workspace Viewer REST API.

Covers the ``/api/workspace/{project_id}/...`` surface — file tree, file
content, git diff, and SSE file-change events — wired through:

  * a real ``daemon.api.app`` ASGI app via ``httpx.ASGITransport``
  * a temporary SQLite ``SQLModel`` engine
  * ``SQLModelProjectRepository`` for project lookup
  * the workspace router's ``set_project_repository`` global setter

The router reads ``project.main_directory`` from the repository to find the
workdir for each request; the tests build real on-disk workdirs (regular
directories, git repositories) and verify the public contract.

Run only this file::

    python -m pytest tests/test_workspace_api.py -v

The conftest autouse fixtures (``_ensure_app_state_manager``,
``_ensure_system_default_project_id``) provide the surrounding safety net;
this file owns the workspace-specific test surface and resets module
globals / singletons in teardown so other tests start clean.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlmodel import SQLModel, create_engine

from daemon.api import app as fastapi_app
from daemon.repositories import SQLModelProjectRepository
from daemon.routers import workspace as workspace_module
from daemon.services.file_change_monitor import FileChangeMonitor
from daemon.services.workspace_guard import WorkspaceGuard


# ============================================================================
# SSE-aware ASGI wrapper
# ============================================================================
#
# httpx's ``ASGITransport`` awaits the entire ASGI app until the app sends
# ``more_body=False`` (see ``httpx/_transports/asgi.py:170``). sse_starlette's
# ``EventSourceResponse`` iterates an async generator and only signals
# ``more_body=False`` when the generator ends — but our workspace SSE
# generator is an infinite ``while True`` loop, so the await never returns
# and the test hangs.
#
# To work around this we wrap the FastAPI app with a small middleware that
# bounds the body duration: once the response has been started, we run the
# inner app in a task with an ``asyncio.wait_for`` ceiling. If the timeout
# fires we forcibly send an empty ``more_body=False`` chunk to release
# httpx, then cancel the inner task. The test receives whatever body
# chunks were buffered up to that point — exactly the determinism the
# SSE_TIMEOUT_S patch provides. Non-SSE routes are unaffected.


class _StreamTimeoutASGI:
    """ASGI wrapper that terminates long-running SSE responses after a timeout.

    Only routes whose path ends in ``/events`` get the timeout treatment —
    every other route is forwarded unchanged.
    """

    def __init__(self, inner, *, timeout: float):
        self.inner = inner
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").endswith("/events"):
            await self.inner(scope, receive, send)
            return

        finished = asyncio.Event()
        started = asyncio.Event()
        saw_more_body = False

        async def wrapped_send(message):
            nonlocal saw_more_body
            if message["type"] == "http.response.start":
                started.set()
            elif message["type"] == "http.response.body":
                # Mark every body chunk that has more_body=False as the
                # natural end-of-stream. For SSE sse_starlette will set
                # more_body=True on every chunk and never set it False,
                # so we'll usually hit the timeout branch below.
                if not message.get("more_body", True):
                    saw_more_body = True
            await send(message)

        async def run_inner():
            try:
                await self.inner(scope, receive, wrapped_send)
            finally:
                finished.set()

        task = asyncio.create_task(run_inner())
        try:
            # Wait for either natural end-of-stream OR the timeout.
            try:
                await asyncio.wait_for(started.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                # The app didn't even send headers in time — let the
                # outer wait_for handle it via the cancel path.
                pass
            try:
                await asyncio.wait_for(finished.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                # Force end-of-stream so httpx returns a Response.
                try:
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"",
                            "more_body": False,
                        }
                    )
                except Exception:
                    pass
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        _ = saw_more_body  # marker; no further use


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def db_path():
    """Temporary SQLite database file (one per test)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def engine(db_path):
    """SQLite SQLModel engine with all tables created for workspace tests."""
    eng = create_engine(f"sqlite:///{db_path}", echo=False)
    # Import the project models so SQLModel.metadata knows about them BEFORE
    # create_all runs; without this the projects table is silently skipped.
    from daemon.repositories.project.models import (  # noqa: F401  (import for side effects)
        Project,
        ProjectMetadataRecord,
        ProjectShortnameLink,
        ProjectStatus,
        ProjectTagLink,
        ProjectType,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def workdir():
    """A real on-disk workdir that the project points at.

    Layout::

        workdir/
            src/
                hello.py
            node_modules/
                ignored.js          # must be filtered out by tree
            .git/
                HEAD               # must be filtered out by tree
            binary.bin            # bytes for the binary-content test
            big.txt               # will exceed monkeypatched MAX_FILE_SIZE_BYTES
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "hello.py").write_text(
            "def hello():\n    return 'world'\n", encoding="utf-8"
        )
        # Ignored directories — must NOT appear in tree responses.
        (root / "node_modules").mkdir()
        (root / "node_modules" / "ignored.js").write_text("// ignored", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        # Binary file (bytes containing non-UTF-8 high bits).
        (root / "binary.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        # "Big" file — populated at test time to exceed monkeypatched limit.
        (root / "big.txt").write_text("placeholder", encoding="utf-8")
        yield root


@pytest_asyncio.fixture
async def client(engine, workdir) -> AsyncIterator[tuple[httpx.AsyncClient, SQLModelProjectRepository, str]]:
    """Async HTTP client + repo + project_id of a project pointing at ``workdir``.

    Each test gets a fresh repository, fresh project row, fresh workdir.
    The autouse ``_ensure_app_state_manager`` fixture in ``tests/conftest.py``
    already supplies a default ``app.state.manager`` so middleware that pokes
    at it (write-pause gates, lifespan stubs) doesn't blow up.

    The FastAPI app is wrapped in ``_StreamTimeoutASGI`` so the SSE test
    can deterministically receive ``connected`` + ``keepalive`` without
    the ASGITransport hanging on an infinite stream (see the wrapper's
    docstring for the full rationale).
    """
    # Build repository + project row
    repo = SQLModelProjectRepository(engine)
    project = repo.create(
        name="workspace-test-project",
        main_directory=str(workdir),
    )
    project_id = project.project_id

    # Wire repository into workspace router
    workspace_module.set_project_repository(repo)

    # Wrap app so SSE responses are bounded in time. 1.0s is comfortably
    # more than 2 × SSE_TIMEOUT_S (set to 0.1 by the SSE test) and short
    # enough that a test failure won't leave the suite hanging.
    asgi_app = _StreamTimeoutASGI(fastapi_app, timeout=1.0)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi_app),
            base_url="http://testserver",
        ) as ac:
            yield ac, repo, project_id
    finally:
        # Reset module-level global so other tests start from a known state.
        # Direct attribute reset sidesteps the strict type hint on the setter.
        workspace_module._project_repo = None
        # Clear the FileChangeMonitor singleton dict so the next test's
        # workdir doesn't share state with this one.
        FileChangeMonitor._instances.clear()


@pytest_asyncio.fixture
async def client_no_main_directory(engine) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Async client + project_id for a project WITHOUT ``main_directory`` set.

    Used to verify the 400 path on the workspace endpoints.
    """
    repo = SQLModelProjectRepository(engine)
    project = repo.create(
        name="workspace-test-no-main-dir",
        main_directory=None,
    )
    project_id = project.project_id
    workspace_module.set_project_repository(repo)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as ac:
            yield ac, project_id
    finally:
        workspace_module._project_repo = None
        FileChangeMonitor._instances.clear()


# ============================================================================
# Helpers
# ============================================================================


def _create_temp_git_repo(tmpdir: Path) -> Path:
    """Initialise a git repo at ``tmpdir`` with a local identity + one commit.

    Returns the repo root (the same ``tmpdir``). The repo has:
      * a committed ``tracked.py`` file with content ``"v1\\n"``
      * local ``user.name`` / ``user.email`` so the commit succeeds in CI
        environments where global git config is missing.

    Caller is responsible for editing files / inspecting diff afterwards.
    """
    tmpdir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Pin a local-only identity so this never touches global git config and
    # never leaks into the host user's gitconfig.
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=str(tmpdir),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Workspace Test"],
        cwd=str(tmpdir),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "workspace-test@example.com"],
        cwd=str(tmpdir),
        env=env,
        check=True,
        capture_output=True,
    )
    (tmpdir / "tracked.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "tracked.py"],
        cwd=str(tmpdir),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(tmpdir),
        env=env,
        check=True,
        capture_output=True,
    )
    return tmpdir


# ============================================================================
# GET /api/workspace/{project_id}/tree
# ============================================================================


class TestGetFileTree:
    """``GET /api/workspace/{project_id}/tree`` returns a recursive tree."""

    @pytest.mark.asyncio
    async def test_tree_root_returns_files_and_directories_and_ignores_git_and_node_modules(
        self, client, workdir
    ):
        """The tree surfaces ``src/``, ``binary.bin``, ``big.txt`` etc., but
        ``.git/`` and ``node_modules/`` MUST be filtered out."""
        ac, _, project_id = client

        response = await ac.get(f"/api/workspace/{project_id}/tree")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["project_id"] == project_id
        assert data["path"] == "."

        names = [node["name"] for node in data["tree"]]
        assert "src" in names
        assert "binary.bin" in names
        assert "big.txt" in names
        # The ignore set is filtering both of these out:
        assert ".git" not in names
        assert "node_modules" not in names

        # Children of ``src`` are expanded at the default depth.
        src_node = next(n for n in data["tree"] if n["name"] == "src")
        assert src_node["type"] == "directory"
        child_names = [c["name"] for c in (src_node.get("children") or [])]
        assert child_names == ["hello.py"]

    @pytest.mark.asyncio
    async def test_tree_subdirectory_path(self, client):
        """``path=src`` returns just that subdirectory's children."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/tree", params={"path": "src"}
        )

        assert response.status_code == 200
        data = response.json()
        names = [node["name"] for node in data["tree"]]
        assert names == ["hello.py"]
        assert data["path"] == "src"

    @pytest.mark.asyncio
    async def test_tree_explicit_dot_is_root_alias(self, client):
        """``path=.`` (explicit) is identical to the omitted-default root tree.

        S5/e2e regression guard: the workspace frontend sends an explicit
        ``path=.`` query param (``WorkspaceService.getFileTree`` default).
        This pins that the explicit form resolves to the workdir root —
        same tree as omitting the param — rather than being rejected.
        """
        ac, _, project_id = client

        explicit = await ac.get(
            f"/api/workspace/{project_id}/tree", params={"path": "."}
        )
        implicit = await ac.get(f"/api/workspace/{project_id}/tree")

        assert explicit.status_code == 200, explicit.text
        assert implicit.status_code == 200, implicit.text
        assert explicit.json()["tree"] == implicit.json()["tree"]
        assert explicit.json()["path"] == "."

    @pytest.mark.asyncio
    async def test_tree_dot_dot_traversal_still_rejected(self, client):
        """``path=./.`` variants that canonicalize to the root are NOT escapes.

        Companion to the dot-alias test: after ``resolve()``, ``./`` and
        ``.`` both canonicalize to the workdir root and must serve the
        tree. Only paths resolving OUTSIDE the boundary 403.
        """
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/tree", params={"path": "./"}
        )

        assert response.status_code == 200, response.text
        assert [n["name"] for n in response.json()["tree"]]

    @pytest.mark.asyncio
    async def test_tree_traversal_rejected(self, client):
        """``GET /tree?path=../../../etc`` must return 403."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/tree",
            params={"path": "../../../etc"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_tree_absolute_path_outside_rejected(self, client):
        """``GET /tree?path=/etc`` must return 403."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/tree", params={"path": "/etc"}
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_tree_node_has_metadata_for_files(self, client):
        """File nodes include their ``size`` (None for directories)."""
        ac, _, project_id = client

        response = await ac.get(f"/api/workspace/{project_id}/tree")

        assert response.status_code == 200
        data = response.json()
        binary_node = next(n for n in data["tree"] if n["name"] == "binary.bin")
        assert binary_node["type"] == "file"
        assert binary_node["size"] == 6  # the bytes we wrote
        assert binary_node["path"] == "binary.bin"


# ============================================================================
# GET /api/workspace/{project_id}/file
# ============================================================================


class TestGetFileContent:
    """``GET /api/workspace/{project_id}/file`` returns content + metadata."""

    @pytest.mark.asyncio
    async def test_file_returns_content_and_metadata(self, client):
        """Text file: content + line count + language + offset/limit echoed."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": "src/hello.py"},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["project_id"] == project_id
        assert data["path"] == "src/hello.py"
        assert "def hello():" in data["content"]
        assert "return 'world'" in data["content"]
        assert data["language"] == "python"
        assert data["total_lines"] == 2
        assert data["offset"] == 1
        assert data["limit"] == 2000
        assert data["truncated"] is False
        assert data["binary"] is False
        assert data["size_bytes"] > 0

    @pytest.mark.asyncio
    async def test_file_traversal_returns_403(self, client):
        """A path that escapes workdir via ``..`` is rejected with 403.

        The production temp-directory allowance remains enabled: this exact
        traversal resolves outside both the workdir and the allowed temp tree.
        """
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": "../../../etc/passwd"},
        )

        assert response.status_code == 403
        detail = response.json().get("detail") or response.json()
        assert "escapes workdir" in (detail.get("error", "") if isinstance(detail, dict) else str(detail))

    @pytest.mark.asyncio
    async def test_file_absolute_path_outside_workdir_returns_403(self, client):
        """An absolute path pointing outside the workdir must be rejected.

        Critical security regression test: ``/etc/passwd`` must NOT leak via
        ``GET /api/workspace/{project_id}/file?path=/etc/passwd``. The router
        uses ``WorkspaceGuard.resolve_strict()`` which enforces containment even
        for absolute paths (unlike ``resolve()`` which trusts absolute paths
        for agent tools).
        """
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": "/etc/passwd"},
        )

        assert response.status_code == 403
        detail = response.json().get("detail") or response.json()
        assert "escapes workdir" in (detail.get("error", "") if isinstance(detail, dict) else str(detail))
        # And — explicitly — the response body must NOT contain /etc/passwd contents.
        body_text = response.text
        assert "root:" not in body_text, (
            f"/etc/passwd content leaked through absolute path: {body_text!r}"
        )

    @pytest.mark.asyncio
    async def test_file_absolute_path_inside_workdir_still_works(
        self, client, workdir
    ):
        """An absolute path that RESOLVES inside the workdir should still serve content.

        ``resolve_strict()`` enforces containment uniformly — it does not
        reject absolute paths outright, only ones that escape the boundary.
        A legitimate absolute path that points at a file inside the workdir
        must be served normally.
        """
        ac, _, project_id = client
        absolute_inside = str((workdir / "src" / "hello.py").resolve())

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": absolute_inside},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert "def hello():" in data["content"]
        assert data["binary"] is False

    @pytest.mark.asyncio
    async def test_oversized_file_returns_413(self, client, workdir, monkeypatch):
        """A file larger than ``MAX_FILE_SIZE_BYTES`` (monkeypatched) → 413."""
        # Shrink the budget so our placeholder exceeds it after we grow it.
        monkeypatch.setattr(WorkspaceGuard, "MAX_FILE_SIZE_BYTES", 16)
        # Grow ``big.txt`` beyond the patched limit.
        (workdir / "big.txt").write_text("x" * 256, encoding="utf-8")

        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": "big.txt"},
        )

        assert response.status_code == 413
        detail = response.json().get("detail") or response.json()
        assert detail.get("error") == "file_too_large"

    @pytest.mark.asyncio
    async def test_binary_file_returns_binary_true(self, client):
        """Binary bytes file → ``binary: true`` and empty content."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": "binary.bin"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["binary"] is True
        assert data["content"] == ""
        # We know the byte count from the fixture.
        assert data["size_bytes"] == 6


# ============================================================================
# PUT /api/workspace/{project_id}/file
# ============================================================================


class TestPutFileContent:
    """``PUT /api/workspace/{project_id}/file`` writes file content to disk."""

    @pytest.mark.asyncio
    async def test_put_new_file_writes_content_to_disk(self, client, workdir):
        """Writing a brand-new file → 200, response echoes path/size, file exists."""
        ac, _, project_id = client
        target = workdir / "written.py"
        body_text = "print('hello, world')\n"

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "written.py", "content": body_text},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["project_id"] == project_id
        assert data["path"] == "written.py"
        assert data["saved"] is True
        assert data["size_bytes"] == len(body_text.encode("utf-8"))

        # File actually exists on disk with the bytes we sent.
        assert target.exists()
        assert target.read_text(encoding="utf-8") == body_text

    @pytest.mark.asyncio
    async def test_put_overwrites_existing_file(self, client, workdir):
        """Writing to an existing file replaces its content (not appends)."""
        ac, _, project_id = client
        target = workdir / "src" / "hello.py"
        original = target.read_text(encoding="utf-8")
        replacement = "def hello():\n    return 'replaced'\n"
        assert replacement != original  # sanity

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "src/hello.py", "content": replacement},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["size_bytes"] == len(replacement.encode("utf-8"))
        assert target.read_text(encoding="utf-8") == replacement
        # And the original content is gone (no append).
        assert "return 'world'" not in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_put_creates_missing_parent_directories(self, client, workdir):
        """Writing to a nested path creates intermediate directories."""
        ac, _, project_id = client
        nested = workdir / "new_dir" / "sub" / "leaf.txt"
        assert not nested.parent.exists()  # sanity: parent dirs missing
        body_text = "leaf\n"

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "new_dir/sub/leaf.txt", "content": body_text},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["path"] == "new_dir/sub/leaf.txt"
        assert data["size_bytes"] == len(body_text.encode("utf-8"))
        assert nested.exists()
        assert nested.read_text(encoding="utf-8") == body_text

    @pytest.mark.asyncio
    async def test_put_traversal_rejected_and_does_not_escape(
        self, client, tmp_path
    ):
        """A path aimed outside the workdir must be rejected (4xx) and the
        would-be target file must NOT be created on disk."""
        ac, _, project_id = client
        evil_target = tmp_path / "evil.txt"  # outside the project workdir
        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": str(evil_target), "content": "should not leak"},
        )
        assert response.status_code in (400, 403, 404)
        assert not evil_target.exists()

    @pytest.mark.asyncio
    async def test_put_traversal_via_relative_dotdot_rejected(self, client):
        """``path=../../../etc/passwd`` → 403 (matches the GET endpoint's behavior)."""
        ac, _, project_id = client

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "../../../etc/passwd", "content": "should not leak"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_put_absolute_path_outside_workdir_rejected(self, client):
        """``path=/etc/passwd`` → 403 — absolute escapes are rejected too."""
        ac, _, project_id = client

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "/etc/passwd", "content": "should not leak"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_put_oversized_content_returns_413(self, client, workdir, monkeypatch):
        """Content exceeding the (monkeypatched) ``MAX_FILE_SIZE_BYTES`` → 413.

        Mirrors the GET endpoint's ``test_oversized_file_returns_413``
        pattern: shrink the guard's limit so a small payload trips it.
        Also verifies that the pre-existing file on disk is not modified
        by the rejected write.
        """
        monkeypatch.setattr(WorkspaceGuard, "MAX_FILE_SIZE_BYTES", 16)
        ac, _, project_id = client
        original_content = (workdir / "big.txt").read_text(encoding="utf-8")
        oversized = "x" * 256  # well over the patched 16-byte cap

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "big.txt", "content": oversized},
        )

        assert response.status_code == 413
        detail = response.json().get("detail") or response.json()
        assert detail.get("error") == "file_too_large"
        # The rejected write must not have changed the file on disk.
        assert (workdir / "big.txt").read_text(encoding="utf-8") == original_content

    @pytest.mark.asyncio
    async def test_put_empty_content_succeeds_with_zero_size(self, client, workdir):
        """Empty content is allowed; ``size_bytes`` is 0 and the file is empty."""
        ac, _, project_id = client
        target = workdir / "empty.txt"

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "empty.txt", "content": ""},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["saved"] is True
        assert data["size_bytes"] == 0
        assert target.exists()
        assert target.read_text(encoding="utf-8") == ""

    @pytest.mark.asyncio
    async def test_put_unicode_content_preserved(self, client, workdir):
        """Non-ASCII UTF-8 content round-trips: ``size_bytes`` matches byte length."""
        ac, _, project_id = client
        target = workdir / "unicode.txt"
        body_text = "café — 你好 — 🦀\n"

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "unicode.txt", "content": body_text},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["size_bytes"] == len(body_text.encode("utf-8"))
        assert target.read_text(encoding="utf-8") == body_text

    @pytest.mark.asyncio
    async def test_put_permission_error_returns_500_write_failed(
        self, client, workdir, monkeypatch
    ):
        """A filesystem permission failure returns the ``write_failed`` envelope."""
        def _raise_permission(target, content):
            raise PermissionError("simulated permission denied")

        monkeypatch.setattr(workspace_module, "_write_file_safe", _raise_permission)
        ac, _, project_id = client

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "perm.txt", "content": "x"},
        )

        assert response.status_code == 500, response.text
        detail = response.json().get("detail") or response.json()
        assert detail.get("error") == "write_failed"

    @pytest.mark.asyncio
    async def test_put_directory_target_returns_400_path_is_directory(
        self, client, workdir
    ):
        """Writing to a directory is rejected before a temp file is created."""
        ac, _, project_id = client
        assert (workdir / "src").is_dir()

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "src", "content": "should fail"},
        )

        assert response.status_code == 400, response.text
        detail = response.json().get("detail") or response.json()
        assert detail.get("error") == "path_is_directory"
        leaked = [p.name for p in workdir.iterdir() if p.name.endswith(".tmp")]
        assert not leaked, f"Temp file leaked after directory rejection: {leaked}"

    @pytest.mark.asyncio
    async def test_put_atomic_write_preserves_existing_file_on_failure(
        self, client, workdir, monkeypatch
    ):
        """A failed atomic overwrite leaves the existing file intact."""
        target = workdir / "atomic.txt"
        original_content = "ORIGINAL-CONTENT-DO-NOT-LOSE\n"
        target.write_text(original_content, encoding="utf-8")

        # Fail after the real atomic writer has created, written, and fsynced
        # its temporary file, but before it can replace the existing target.
        def _fail_replace(*args, **kwargs):
            raise OSError("simulated crash during os.replace")

        monkeypatch.setattr(workspace_module.os, "replace", _fail_replace)
        ac, _, project_id = client
        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "atomic.txt", "content": "NEW-CONTENT-SHOULD-NOT-WIN\n"},
        )

        assert response.status_code == 500, response.text
        detail = response.json().get("detail") or response.json()
        assert detail.get("error") == "write_failed"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == original_content, (
            "Existing file was truncated or overwritten despite atomic-write failure"
        )
        leaked = list(workdir.glob(".*.tmp")) + list(workdir.glob("*.tmp"))
        assert not leaked, f"Temp file leaked after failed write: {leaked}"

    @pytest.mark.asyncio
    async def test_put_unknown_project_returns_404(self, client):
        """``PUT`` against an unknown ``project_id`` → 404, no side effects."""
        ac, _, _ = client

        response = await ac.put(
            "/api/workspace/no-such-project-id-12345/file",
            json={"path": "x.txt", "content": "x"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_put_missing_content_field_returns_422(self, client, workdir):
        """Omitting the required ``content`` field → 422 (Pydantic validation).

        Pins the contract: ``content`` is a required field of ``FileWriteRequest``.
        """
        ac, _, project_id = client

        response = await ac.put(
            f"/api/workspace/{project_id}/file",
            json={"path": "x.txt"},
        )

        assert response.status_code == 422


# ============================================================================
# GET /api/workspace/{project_id}/diff
# ============================================================================


class TestGetFileDiff:
    """``GET /api/workspace/{project_id}/diff`` returns git diffs."""

    @pytest.mark.asyncio
    async def test_diff_modified_git_file_returns_diff(self, engine, workdir):
        """Modified file in a git repo → ``has_changes=True`` + non-empty diff.

        The flow:
          1. Create a fresh git repo in a sibling tempdir
          2. Configure a local git identity
          3. Commit ``tracked.py`` with content ``"v1\\n"``
          4. Edit the file to ``"v2\\n"``
          5. Hit ``/diff`` and verify the response contains the change.
        """
        with tempfile.TemporaryDirectory() as git_parent:
            git_parent = Path(git_parent)
            # Replace the workdir's git setup with our freshly-initialised repo.
            # We re-use the existing workdir so the project row's main_directory
            # already points there.
            _create_temp_git_repo(workdir)
            # Edit the committed file so a real diff exists.
            (workdir / "tracked.py").write_text("v2\n", encoding="utf-8")

            repo = SQLModelProjectRepository(engine)
            workspace_module.set_project_repository(repo)
            try:
                project = repo.create(
                    name="workspace-git-test",
                    main_directory=str(workdir),
                )
                project_id = project.project_id

                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=fastapi_app),
                    base_url="http://testserver",
                ) as ac:
                    response = await ac.get(
                        f"/api/workspace/{project_id}/diff",
                        params={"path": "tracked.py"},
                    )

                assert response.status_code == 200, response.text
                data = response.json()
                assert data["project_id"] == project_id
                assert data["has_changes"] is True
                assert data["error"] is None
                assert data["diff"] is not None
                # The diff should mention the lines we changed.
                assert "v1" in data["diff"]
                assert "v2" in data["diff"]
                # HEAD content is the committed version; working content is
                # the freshly-edited one.
                assert data["head_content"] == "v1\n"
                assert data["working_content"] == "v2\n"
            finally:
                workspace_module._project_repo = None
                FileChangeMonitor._instances.clear()
            # Reference git_parent so the unused variable doesn't trip linting.
            _ = git_parent

    @pytest.mark.asyncio
    async def test_diff_nonexistent_file_returns_file_not_found(self, engine, workdir):
        """A path that doesn't exist on disk or in HEAD → error=``file_not_found``.

        Regression test: previously, a non-existent file returned
        ``has_changes=True`` with an empty diff because ``head_content is
        None`` inflated the ``has_changes`` flag. The fix detects that the
        file is absent from both HEAD and the working tree.

        Uses the same git-repo setup as the modified-file test so the
        ``is_git_repo`` guard passes before reaching the file-existence
        check.
        """
        _create_temp_git_repo(workdir)
        repo = SQLModelProjectRepository(engine)
        workspace_module.set_project_repository(repo)
        try:
            project = repo.create(
                name="workspace-git-test-nonexistent",
                main_directory=str(workdir),
            )
            project_id = project.project_id

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=fastapi_app),
                base_url="http://testserver",
            ) as ac:
                response = await ac.get(
                    f"/api/workspace/{project_id}/diff",
                    params={"path": "totally/fake/nonexistent.py"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["error"] == "file_not_found"
            assert data["has_changes"] is False
            assert data["diff"] is None
        finally:
            workspace_module._project_repo = None
            FileChangeMonitor._instances.clear()

    @pytest.mark.asyncio
    async def test_diff_non_git_repo_returns_error_not_a_git_repo(self, client):
        """A non-git workdir → ``error="not_a_git_repo"``, ``has_changes=False``."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/diff",
            params={"path": "src/hello.py"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["error"] == "not_a_git_repo"
        assert data["has_changes"] is False
        assert data["diff"] is None

    @pytest.mark.asyncio
    async def test_diff_traversal_rejected(self, client):
        """``GET /diff?path=../../../etc/passwd`` must return 403."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/diff",
            params={"path": "../../../etc/passwd"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_diff_temp_dir_rejected(self, client):
        """``GET /diff?path=/tmp/secret`` must return 403."""
        ac, _, project_id = client

        response = await ac.get(
            f"/api/workspace/{project_id}/diff",
            params={"path": "/tmp/secret.txt"},
        )

        assert response.status_code == 403


# ============================================================================
# GET /api/workspace/{project_id}/events  (SSE)
# ============================================================================


class TestWorkspaceEventsSSE:
    """``GET /api/workspace/{project_id}/events`` streams file-change events."""

    @pytest.mark.asyncio
    async def test_events_sse_emits_connected_then_keepalive(
        self, client, monkeypatch
    ):
        """The SSE stream emits a ``connected`` event first, then a
        ``keepalive`` event after ``SSE_TIMEOUT_S`` of inactivity.

        We patch ``workspace_module.SSE_TIMEOUT_S`` down to a tiny value so the
        keepalive fires promptly and the ASGITransport test does not hang.
        """
        # Tiny keepalive interval — keep test deterministic + fast.
        monkeypatch.setattr(workspace_module, "SSE_TIMEOUT_S", 0.1)
        ac, _, project_id = client

        events_seen: list[str] = []
        # Use httpx's stream context so the response gets cleanly closed
        # when we exit (which lets the server-side generator run its finally
        # block to remove the FileChangeMonitor subscriber).
        async with ac.stream(
            "GET", f"/api/workspace/{project_id}/events"
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            # Read SSE lines until we've seen both ``connected`` and
            # ``keepalive``, or until we've consumed a generous cap of
            # lines. ``aiter_lines`` is the async variant — the sync
            # ``iter_lines`` would raise on an async stream.
            max_lines = 200
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                    events_seen.append(event_name)
                if "connected" in events_seen and "keepalive" in events_seen:
                    break
                max_lines -= 1
                if max_lines <= 0:
                    break

        assert "connected" in events_seen, (
            f"Did not receive 'connected' event; saw {events_seen}"
        )
        assert "keepalive" in events_seen, (
            f"Did not receive 'keepalive' event; saw {events_seen}"
        )


# ============================================================================
# Error / 4xx paths
# ============================================================================


class TestWorkspaceErrors:
    """400 / 404 error envelopes from the workspace router."""

    @pytest.mark.asyncio
    async def test_project_without_main_directory_returns_400(self, client_no_main_directory):
        """Project with no ``main_directory`` → 400."""
        ac, project_id = client_no_main_directory

        response = await ac.get(f"/api/workspace/{project_id}/tree")

        assert response.status_code == 400
        detail = response.json().get("detail") or response.json()
        assert "main_directory" in detail.get("error", "")

    @pytest.mark.asyncio
    async def test_project_without_main_directory_file_returns_400(
        self, client_no_main_directory
    ):
        """Same 400 envelope on the file endpoint."""
        ac, project_id = client_no_main_directory

        response = await ac.get(
            f"/api/workspace/{project_id}/file",
            params={"path": "anywhere.txt"},
        )

        assert response.status_code == 400
        detail = response.json().get("detail") or response.json()
        assert "main_directory" in detail.get("error", "")

    @pytest.mark.asyncio
    async def test_missing_project_returns_404(self, client):
        """Unknown project_id → 404."""
        ac, _, _ = client

        response = await ac.get("/api/workspace/no-such-project-id-12345/tree")

        assert response.status_code == 404
        detail = response.json().get("detail") or response.json()
        assert "not found" in detail.get("error", "").lower()