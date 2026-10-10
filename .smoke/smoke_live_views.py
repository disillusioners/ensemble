"""Minimal smoke harness for the live_views router — bypasses lifespan
(DL, LLM, manager, etc.) and mounts the router on a fresh app
with a tiny test config.

Used to verify the Phase 2 URL chain + markdown wrapper in a real
HTTP server without booting the full daemon. Pick a free port
(18079 by default — the spec notes 8079 may collide with a running
daemon); bind to 127.0.0.1 only.

Run from the worktree root (or anywhere — the worktree is derived
from this file's location; override with LIVE_VIEWS_SMOKE_WORKTREE
when running from a copied/installed tree):

    .venv/bin/python .smoke/smoke_live_views.py [PORT]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

# Worktree-root Python — make sure the worktree's daemon package is
# importable regardless of the caller's CWD. The script lives at
# ``<worktree>/.smoke/smoke_live_views.py``, so parents[1] is the
# worktree root. Override via LIVE_VIEWS_SMOKE_WORKTREE (e.g. when
# the harness is copied into a non-worktree location).
WORKTREE = Path(
    os.environ.get("LIVE_VIEWS_SMOKE_WORKTREE", "")
    or Path(__file__).resolve().parents[1]
)
sys.path.insert(0, str(WORKTREE))

from daemon.middleware.host_capture import HostCaptureMiddleware  # noqa: E402


def main() -> int:
    from daemon.config import (
        LiveViewsConfig,
        LiveViewsRootConfig,
    )
    from daemon.routers.live_views import build_router
    from daemon.services.live_views import (
        HostRecorder,
        LiveViewsService,
    )

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18079

    # Stage a real .md file under a planning-style subtree of the
    # worktree, so the smoke harness can serve it via the
    # ``planning`` root.
    workdir = WORKTREE
    sample_rel = "smoke/sample.md"
    sample_path = (
        workdir
        / ".agents"
        / "shared"
        / "planning"
        / "smoke"
        / "sample.md"
    )
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(
        "# Phase 2 smoke\n\n"
        "Hello from a markdown file served via the live_views router.\n\n"
        "Payloads like `<script>alert(1)</script>` and "
        "[x](javascript:alert(1)) must NOT survive into the wrapper "
        "as live executable HTML.\n"
    )

    cfg = LiveViewsConfig()
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
    )
    # The seed ``designer-artifact`` root also requires the
    # ``design/mockups`` contiguous subpath; remove it so the
    # smoke doesn't need a separate mockups dir.
    cfg.roots.pop("designer-artifact", None)

    recorder = HostRecorder()

    def _resolve_project_workdir(shortname: str) -> str | None:
        if shortname == "ens":
            return str(workdir)
        return None

    service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=_resolve_project_workdir,
        bind_host="127.0.0.1",
        bind_port=port,
        host_recorder=recorder,
    )

    app = FastAPI()
    app.state.live_views_service = service
    app.state.host_recorder = recorder
    # HostCaptureMiddleware is imported at module scope (from
    # daemon.middleware.host_capture, after the sys.path fix-up) —
    # the same class the daemon wires in create_app(). The recorder
    # resolution is identical — scope["app"].state.host_recorder.
    app.add_middleware(HostCaptureMiddleware)
    app.include_router(build_router())

    # Inline the /views/livez operator probe so the smoke can ping
    # it the same way a deployment would.
    from fastapi.responses import JSONResponse

    from daemon.routers.live_views import _uniform_404

    @app.get("/views/livez", include_in_schema=False)
    async def _livez():
        svc = app.state.live_views_service
        if svc is None or not svc.enabled():
            return _uniform_404()
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "enabled": True,
                "roots": svc.root_names(),
            },
        )

    # Mint a sample link via the service directly (mirrors the
    # ``view_link`` tool surface; the tool requires a manager).
    sample_link = service.build_url("planning", "ens/smoke/sample.md")
    print(f"[smoke] sample_link={sample_link}")
    print(f"[smoke] listening on http://127.0.0.1:{port}")

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())