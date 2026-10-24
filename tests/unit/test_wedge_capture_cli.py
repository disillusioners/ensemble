"""CLI pack — the thin operator client (T37 / AC53 / D34 / AC38).

WHAT THIS PACK PROVES
---------------------
* the FOUR subcommands (``list``, ``get``, ``capture``, ``status``) work
  against a REAL (in-process) HTTP server — a fake API, never a DB;
* the documented exit codes: ``0`` ok / ``1`` refused-or-not-found /
  ``2`` usage / ``3`` transport error;
* ``--json`` is a raw passthrough;
* ``capture`` SUBMITS and RETURNS the 202's pre-allocated id — it does
  NOT wait for, or poll after, the capture (N6: enqueue-then-poll is the
  operator's next command, not the CLI's loop);
* the **thin-client property** (AC38): no DB import, no direct read of
  the capture root — the script speaks only HTTP.

SAFETY FENCE: the server binds ``127.0.0.1`` on an EPHEMERAL port and
serves canned JSON. Nothing touches a capture directory or a database.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "scripts" / "wedge_capture.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("wedge_capture_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cli = _load_cli()

CAPTURE_ID = "20261022T120000Z-host01-manual-abcdef01"


# ── the fake API ───────────────────────────────────────────────────────────

LIST_BODY = {
    "captures": [
        {
            "capture_id": CAPTURE_ID,
            "captured_at": "20261022T120000Z",
            "trigger_reason": "manual",
            "trigger_source": "manual",
            "boot_epoch": "2026-10-22T11:00:00",
            "host": "host01",
            "summary": "operator drill",
            "auto_continue_mode": "on",
            "file_count": 8,
            "total_bytes": 4096,
        }
    ],
    "count": 1,
    "source": "filesystem",
}

GET_BODY = {
    "capture_id": CAPTURE_ID,
    "capture_dir": "/tmp/does-not-matter/" + CAPTURE_ID,
    "manifest": {"trigger_reason": "manual", "auto_continue_mode": "on"},
    "artifacts": {"manifest.json": {"status": "ok"}},
    "row": None,
    "auto_continue_mode": "on",
}

STATUS_BODY = {
    "enabled": True,
    "running": True,
    "auto_continue_mode": "on",
    "boot_epoch": "2026-10-22T11:00:00",
    "missed_samples": 0,
    "last_capture_time": None,
    "cpu_pct": 95.0,
    "lag_s": 5.0,
}


class _Handler(BaseHTTPRequestHandler):
    """A canned wedge-captures API. Records every request it serves."""

    server_version = "FakeWedgeCapture/1.0"

    def log_message(self, *args: Any) -> None:  # silence stderr noise
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the http.server contract
        self.server.requests.append(("GET", self.path))  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/wedge-captures/status":
            self._send(200, STATUS_BODY)
        elif path == "/api/wedge-captures":
            self._send(200, LIST_BODY)
        elif path.endswith("/20261022T120000Z-host01-manual-aaaaaaaa"):
            self._send(
                404,
                {"detail": {"error": "not_found", "message": "no capture matches"}},
            )
        elif path.startswith("/api/wedge-captures/"):
            self._send(200, GET_BODY)
        else:
            self._send(404, {"detail": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802 - the http.server contract
        self.server.requests.append(("POST", self.path))  # type: ignore[attr-defined]
        self._send(
            202,
            {
                "status": "accepted",
                "capture_id": CAPTURE_ID,
                "capture_dir": "/tmp/does-not-matter/" + CAPTURE_ID,
                "reason": "manual",
                "enqueued": True,
                "note": "enqueued — the watchdog services it on its next tick",
            },
        )


@pytest.fixture()
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# ── the four subcommands ──────────────────────────────────────────────────


class TestSubcommands:
    def test_list_renders_a_table(self, fake_api, capsys) -> None:
        _, base = fake_api
        assert cli.main(["--base-url", base, "list"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert CAPTURE_ID in out
        assert "manual" in out
        assert "1 capture(s)" in out

    def test_list_json_is_a_raw_passthrough(self, fake_api, capsys) -> None:
        _, base = fake_api
        assert cli.main(["--base-url", base, "list", "--json"]) == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload == LIST_BODY

    def test_get_prints_the_payload_summary(self, fake_api, capsys) -> None:
        _, base = fake_api
        assert cli.main(["--base-url", base, "get", CAPTURE_ID]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert CAPTURE_ID in out

    def test_get_unknown_exits_one(self, fake_api, capsys) -> None:
        _, base = fake_api
        code = cli.main(
            ["--base-url", base, "get", "20261022T120000Z-host01-manual-aaaaaaaa"]
        )
        assert code == cli.EXIT_NOT_FOUND
        assert "REFUSED" in capsys.readouterr().err

    def test_status_reports_the_mode(self, fake_api, capsys) -> None:
        _, base = fake_api
        assert cli.main(["--base-url", base, "status"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "on" in out
        assert "auto" in out.lower()

    def test_capture_returns_the_202_without_polling(
        self, fake_api, capsys
    ) -> None:
        """N6: the CLI submits and returns; it never waits or polls."""
        server, base = fake_api
        assert cli.main(["--base-url", base, "capture"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert CAPTURE_ID in out
        assert "enqueu" in out.lower()
        # exactly ONE request: the submit. No follow-up list/get poll.
        assert server.requests == [("POST", "/api/wedge-captures/capture")]

    def test_capture_refusal_surfaces_the_token_verbatim(self, capsys) -> None:
        """A 409 ``capture_busy`` is a typed outcome, not a crash."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RefusingHandler)
        server.requests = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            code = cli.main(["--base-url", base, "capture"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

        assert code == cli.EXIT_NOT_FOUND
        assert "capture_busy" in capsys.readouterr().err


class _RefusingHandler(_Handler):
    def do_POST(self) -> None:  # noqa: N802
        self.server.requests.append(("POST", self.path))  # type: ignore[attr-defined]
        self._send(
            409,
            {
                "detail": {
                    "error": "capture_busy",
                    "message": "the manual-capture slot is occupied",
                }
            },
        )


class TestExitCodes:
    def test_a_transport_error_exits_three(self, capsys) -> None:
        # port 1 on loopback: nothing listens there, the connect is refused
        code = cli.main(["status", "--base-url", "http://127.0.0.1:1"])
        assert code == cli.EXIT_TRANSPORT
        assert "transport error" in capsys.readouterr().err

    def test_an_unknown_subcommand_exits_two(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["frobnicate"])
        assert excinfo.value.code == cli.EXIT_USAGE

    def test_the_exit_code_vocabulary_is_the_documented_four(self) -> None:
        assert (
            cli.EXIT_OK, cli.EXIT_NOT_FOUND, cli.EXIT_USAGE, cli.EXIT_TRANSPORT
        ) == (0, 1, 2, 3)


# ── AC38: the thin-client property ────────────────────────────────────────


class TestThinClient:
    def _imports(self) -> set[str]:
        tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        return modules

    def test_it_imports_no_database_driver(self) -> None:
        imports = self._imports()
        for forbidden in ("psycopg", "psycopg2", "sqlmodel", "sqlalchemy", "asyncpg"):
            assert forbidden not in imports, forbidden

    def test_it_imports_nothing_from_the_daemon_package(self) -> None:
        """The CLI must run on a host with no venv: stdlib only."""
        source = CLI_PATH.read_text(encoding="utf-8")
        assert "import daemon" not in source
        assert "from daemon" not in source
        assert "daemon" not in self._imports()

    def test_it_never_reads_the_capture_root_directly(self) -> None:
        source = CLI_PATH.read_text(encoding="utf-8")
        # The CLI reaches the store ONLY through the HTTP API prefix — it
        # carries no capture-store helper and never opens an artifact.
        assert "API_PREFIX = \"/api/wedge-captures\"" in source
        for forbidden in ("capture_directories", "manifest.json", "read_text()"):
            assert forbidden not in source, forbidden

    def test_the_base_url_precedence_never_hard_codes_production(self) -> None:
        assert cli.resolve_base_url("http://host:1234/") == "http://host:1234"
        assert cli.DEFAULT_BASE_URL.startswith("http://127.0.0.1:")

    def test_the_install_dir_is_explicit_never_home_derived(self) -> None:
        source = CLI_PATH.read_text(encoding="utf-8")
        assert "expanduser" not in source
        assert '"~"' not in source and "'~'" not in source
        # the literal is assembled so this file itself stays grep-clean
        assert ("/" + "home" + "/") not in source
