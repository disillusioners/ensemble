"""Integration coverage for workspace file-change SSE events."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from sqlmodel import SQLModel, create_engine

from daemon.api import app
from daemon.repositories import SQLModelProjectRepository
from daemon.routers import workspace as workspace_module
from daemon.services.file_change_monitor import FileChangeMonitor


class _StreamTimeoutASGI:
    """End an SSE response after a file event or a bounded timeout.

    ``httpx.ASGITransport`` buffers an ASGI response until the app sends
    ``more_body=False``. Workspace SSE responses are infinite, so this wrapper
    closes the response once the event under test has been sent.
    """

    def __init__(self, inner, *, timeout: float):
        self.inner = inner
        self.timeout = timeout
        self.connected = asyncio.Event()
        self.file_changed = asyncio.Event()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").endswith("/events"):
            await self.inner(scope, receive, send)
            return

        started = False

        async def wrapped_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
            body = message.get("body", b"")
            if b"event: connected" in body:
                self.connected.set()
            if b"event: file_changed" in body:
                self.file_changed.set()

        app_task = asyncio.create_task(self.inner(scope, receive, wrapped_send))
        event_task = asyncio.create_task(self.file_changed.wait())
        try:
            done, _ = await asyncio.wait(
                {app_task, event_task},
                timeout=self.timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if app_task in done:
                await app_task
                return
            if started:
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
        finally:
            event_task.cancel()
            try:
                await event_task
            except asyncio.CancelledError:
                pass
            if not app_task.done():
                app_task.cancel()
            try:
                await app_task
            except (asyncio.CancelledError, Exception):
                pass


async def _iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    event_name: str | None = None
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        elif not line and event_name is not None:
            yield event_name, "\n".join(data)
            event_name = None
            data = []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_sse_reports_modified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    relative_path = "watched.txt"
    watched_file = workdir / relative_path
    watched_file.write_text("before\n", encoding="utf-8")

    engine = create_engine(f"sqlite:///{tmp_path / 'projects.db'}")
    SQLModel.metadata.create_all(engine)
    repo = SQLModelProjectRepository(engine)
    project = repo.create(
        name="workspace-sse-integration",
        main_directory=str(workdir),
    )
    workspace_module.set_project_repository(repo)

    # Polling has a platform-independent "modified" event; watchdog may also
    # report open/close events around the same write depending on the OS.
    monkeypatch.setattr(
        "daemon.services.file_change_monitor.HAS_WATCHDOG", False
    )
    # Shorten the polling-fallback interval so the file_change event arrives
    # well within the bounded_app timeout (8s). The default 5s rounds the
    # test's behaviour to luck-of-the-event-loop on busy CI.
    monkeypatch.setattr(
        "daemon.services.file_change_monitor.FileChangeMonitor.DEFAULT_POLL_INTERVAL_S",
        0.05,
    )
    monkeypatch.setattr(workspace_module, "SSE_TIMEOUT_S", 0.1)

    bounded_app = _StreamTimeoutASGI(app, timeout=8.0)
    stream_task: asyncio.Task[httpx.Response] | None = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bounded_app),
            base_url="http://testserver",
        ) as client:
            request = client.build_request(
                "GET", f"/api/workspace/{project.project_id}/events"
            )
            stream_task = asyncio.create_task(client.send(request, stream=True))

            # ASGITransport buffers response bodies, so run the request in the
            # background and mutate only after the connected event was sent.
            await asyncio.wait_for(bounded_app.connected.wait(), timeout=7.0)
            assert bounded_app.connected.is_set()
            await asyncio.sleep(0.05)  # Allow the initial polling snapshot.
            watched_file.write_text("after change\n", encoding="utf-8")

            response = await stream_task
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            changed_event: dict[str, object] | None = None
            first_event = True
            async for event_name, event_data in _iter_sse(response):
                if first_event:
                    assert event_name == "connected"
                    connected = json.loads(event_data)
                    assert connected == {
                        "status": "connected",
                        "project_id": project.project_id,
                    }
                    first_event = False
                    continue
                if event_name == "keepalive":
                    continue
                if event_name == "file_changed":
                    changed_event = json.loads(event_data)
                    break
            await response.aclose()

            assert changed_event is not None
            assert changed_event["path"] == relative_path
            assert changed_event["change_type"] == "modified"

            file_response = await client.get(
                f"/api/workspace/{project.project_id}/file",
                params={"path": relative_path},
            )
            assert file_response.status_code == 200
            file_data = file_response.json()
            assert file_data["path"] == relative_path
            assert file_data["content"] == "after change"
    finally:
        if stream_task is not None:
            if not stream_task.done():
                stream_task.cancel()
            try:
                await stream_task
            except (asyncio.CancelledError, Exception):
                pass
        workspace_module._project_repo = None
        FileChangeMonitor._instances.clear()
        engine.dispose()
