"""Tests for SelectiveAccessLogMiddleware access-log suppression.

The queue-status badge feature (commit 432eb56e) introduced two frontend
poll endpoints whose success traffic is pure noise in the access log:

    GET /api/missions
    GET /api/queues/defer-blocked

The middleware's ``HIDE_PATTERNS_SUCCESS_ONLY`` list suppresses these
paths on 2xx/3xx but keeps 4xx/5xx visible so polling failures stay
auditable. This test pins both directions of that contract, plus the
"unrelated paths keep logging" invariant.

The middleware class is closure-local inside ``daemon.api.create_app``,
so the test extracts the class definition via ``inspect.getsource`` and
re-execs it into a fresh namespace. This avoids any production
refactor (the brief forbids middleware-level structural changes) while
still exercising the real production code path.
"""

from __future__ import annotations

from contextlib import contextmanager

import inspect
import logging
import re

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


LOGGER_NAME = "daemon.api"
# Paths the new suppression list must cover.
POLLED_PATHS = ("/api/missions", "/api/queues/defer-blocked")
# An unrelated path that must still log normally — neither in
# HIDE_PATTERNS nor HIDE_METHOD_PATH nor HIDE_PATTERNS_SUCCESS_ONLY.
UNRELATED_PATH = "/api/test-still-logs"


def _extract_middleware_class():
    """Re-exec the SelectiveAccessLogMiddleware class source into a fresh
    namespace and return the class object.

    The class is closure-local inside ``daemon.api.create_app``. To test
    the real production class without refactoring the production code,
    we read the class source verbatim and exec it with a known
    ``logger`` injected (the class body references the module-level
    ``logger`` from daemon.api).

    The source-boundary sentinel is the next top-level statement that
    follows the class — ``# Add CORS``. If the class layout changes
    shape (e.g. extracted to its own module), the sentinel shifts and
    this helper fails loudly.
    """
    from daemon.api import create_app  # noqa: WPS433 — intentional import

    src = inspect.getsource(create_app)
    match = re.search(
        r"class SelectiveAccessLogMiddleware:[\s\S]+?(?=\n    # Add CORS)",
        src,
    )
    assert match is not None, (
        "Could not locate SelectiveAccessLogMiddleware inside "
        "daemon.api.create_app — sentinel '# Add CORS' moved or the "
        "class was extracted. Update this helper."
    )

    namespace: dict = {"logger": logging.getLogger(LOGGER_NAME)}
    exec(match.group(0), namespace)  # noqa: S102 — controlled, reviewed source
    return namespace["SelectiveAccessLogMiddleware"]


# Extract once at module load. The source is stable for the duration of
# this test run; the assertion in the helper above catches breakage.
SelectiveAccessLogMiddleware = _extract_middleware_class()


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app wired with the production middleware
    class plus stub endpoints for the three paths the test exercises.

    The stub endpoints read their status code from ``app.state`` so the
    same path can be exercised at both 200 and 500 within one fixture
    lifetime without redefining routes.
    """
    app = FastAPI()
    app.add_middleware(SelectiveAccessLogMiddleware)

    async def _stub_missions(request: Request) -> JSONResponse:
        code = getattr(request.app.state, "missions_status", 200)
        return JSONResponse(status_code=code, content={"missions": []})

    async def _stub_defer_blocked(request: Request) -> JSONResponse:
        code = getattr(request.app.state, "defer_blocked_status", 200)
        return JSONResponse(
            status_code=code, content={"defer_blocked": []}
        )

    async def _stub_unrelated() -> JSONResponse:
        return JSONResponse(status_code=200, content={"ok": True})

    app.add_api_route("/api/missions", _stub_missions, methods=["GET"])
    app.add_api_route(
        "/api/queues/defer-blocked", _stub_defer_blocked, methods=["GET"]
    )
    app.add_api_route(UNRELATED_PATH, _stub_unrelated, methods=["GET"])

    return app


def _records_for_path(records, path: str):
    """Filter caplog records to daemon.api INFO entries that mention the
    given path. The middleware's log message is one line with the path
    text embedded; substring match is sufficient and resilient to the
    ANSI color codes it wraps around the path-free segments."""
    return [
        r
        for r in records
        if r.name == LOGGER_NAME
        and r.levelno == logging.INFO
        and path in r.getMessage()
    ]


class TestSelectiveAccessLogPollingSuppression:
    """Pin the HIDE_PATTERNS_SUCCESS_ONLY contract end-to-end."""

    def test_missions_200_is_suppressed(self):
        """Successful polling of /api/missions emits no access-log line."""
        app = _build_app()
        with TestClient(app) as client, _capture() as records:
            resp = client.get("/api/missions")

        assert resp.status_code == 200
        assert _records_for_path(records, "/api/missions") == [], (
            "expected no log record for /api/missions 200, got: "
            f"{[r.getMessage() for r in _records_for_path(records, '/api/missions')]}"
        )

    def test_defer_blocked_200_is_suppressed(self):
        """Successful polling of /api/queues/defer-blocked emits no
        access-log line."""
        app = _build_app()
        with TestClient(app) as client, _capture() as records:
            resp = client.get("/api/queues/defer-blocked")

        assert resp.status_code == 200
        assert _records_for_path(records, "/api/queues/defer-blocked") == [], (
            "expected no log record for /api/queues/defer-blocked 200, "
            f"got: {[r.getMessage() for r in _records_for_path(records, '/api/queues/defer-blocked')]}"
        )

    def test_missions_500_is_logged(self):
        """A 5xx response on /api/missions MUST still log so polling
        failures stay visible — the whole point of the
        success-only suppression is that errors escape it."""
        app = _build_app()
        app.state.missions_status = 500

        with TestClient(app) as client, _capture() as records:
            resp = client.get("/api/missions")

        assert resp.status_code == 500
        matching = _records_for_path(records, "/api/missions")
        assert matching, (
            "expected a log record for /api/missions 500 — the "
            "success-only suppression must let error responses through"
        )
        assert any("500" in r.getMessage() for r in matching), (
            f"500 must appear in the log message: {[r.getMessage() for r in matching]}"
        )

    def test_missions_3xx_is_suppressed(self):
        """A 3xx response on /api/missions is SUPPRESSED — pins the
        UPPER boundary of the ``200 <= status_code < 400`` gate so a
        future tightening to ``< 300`` (e.g. a misguided "only 2xx is
        success" refactor) fails loudly here instead of silently
        regressing 3xx polling responses. Pairs with the 200/500
        cases below."""
        app = _build_app()
        app.state.missions_status = 307  # Temporary Redirect

        with TestClient(app) as client, _capture() as records:
            resp = client.get("/api/missions")

        assert resp.status_code == 307
        assert _records_for_path(records, "/api/missions") == [], (
            "expected no log record for /api/missions 307, got: "
            f"{[r.getMessage() for r in _records_for_path(records, '/api/missions')]}"
        )

    def test_defer_blocked_500_is_logged(self):
        """A 5xx response on /api/queues/defer-blocked MUST still log."""
        app = _build_app()
        app.state.defer_blocked_status = 500

        with TestClient(app) as client, _capture() as records:
            resp = client.get("/api/queues/defer-blocked")

        assert resp.status_code == 500
        matching = _records_for_path(records, "/api/queues/defer-blocked")
        assert matching, (
            "expected a log record for /api/queues/defer-blocked 500"
        )

    def test_unrelated_path_200_is_logged(self):
        """An unrelated path that is in NONE of the HIDE lists keeps
        logging exactly as before — proving the new suppression doesn't
        bleed into other endpoints.

        The brief suggested ``/api/instances`` as the example, but that
        path is in the pre-existing ``HIDE_PATTERNS`` list (always
        suppressed) so it cannot serve this role. The fresh test path
        here is intentionally absent from every HIDE list.
        """
        app = _build_app()
        with TestClient(app) as client, _capture() as records:
            resp = client.get(UNRELATED_PATH)

        assert resp.status_code == 200
        matching = _records_for_path(records, UNRELATED_PATH)
        assert matching, (
            f"expected a log record for {UNRELATED_PATH} 200 — unrelated "
            "paths must keep logging"
        )

    def test_polled_path_with_query_string_is_suppressed_on_200(self):
        """The path match must ignore query strings — ASGI's
        ``scope['path']`` already excludes ``scope['query_string']``,
        so a polled endpoint with ``?something=1`` must still match
        ``/api/missions`` exactly and be suppressed on 200."""
        app = _build_app()
        with TestClient(app) as client, _capture() as records:
            resp = client.get("/api/missions?some=1&other=2")

        assert resp.status_code == 200
        assert _records_for_path(records, "/api/missions") == [], (
            "query string must not break the suppression match: "
            f"{[r.getMessage() for r in _records_for_path(records, '/api/missions')]}"
        )


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


@contextmanager
def _capture():
    """Capture daemon.api log records at INFO for the duration of the
    block. Returns a mutable list that the caller inspects after the
    request completes.

    Uses pytest's caplog indirectly via the global LogRecord machinery:
    instead of going through pytest's fixture wiring, we attach a
    handler that just records messages. This keeps each test self-
    contained without depending on caplog propagation through the
    FastAPI/Starlette logger hierarchy.
    """
    records: list[logging.LogRecord] = []
    handler = _RecorderHandler(records)
    handler.setLevel(logging.INFO)

    target = logging.getLogger(LOGGER_NAME)
    target.addHandler(handler)
    try:
        yield records
    finally:
        target.removeHandler(handler)


class _RecorderHandler(logging.Handler):
    def __init__(self, records: list[logging.LogRecord]):
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)