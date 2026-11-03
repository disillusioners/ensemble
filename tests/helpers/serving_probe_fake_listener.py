#!/usr/bin/env python3
"""A FAKE serving listener for the launcher-side serving-probe pins.

WHY A SEPARATE HELPER
---------------------
``tests/helpers/soak_gate_fake_daemon.py`` models the shapes the SOAK
GATE needs — and it answers ``/livez`` 200 in EVERY mode (only ``/readyz``
degrades).  That is exactly right for the soak gate, and exactly WRONG for
the launcher's serving probe: the launcher's canonical GREEN predicate
(OQ-2 / AM-P2-11) needs ``/livez`` 200 ∧ version == the target ∧
``/readyz`` 200 ∧ ready — a fixture that cannot answer with a WRONG
version, a NON-200 ``/livez`` or NO ANSWER AT ALL cannot discriminate a
correct probe from a ``/livez``-only probe.

This helper adds exactly the shapes the old one does not model
(AM-P2-12's mode table):

======================  =====================================  ====================
mode                    behaviour                             classification
======================  =====================================  ====================
``green``               /livez 200 + version, /readyz 200      GREEN
``wrong-version``       /livez 200 with a version != target   AMBIGUOUS (mismatch)
``livez-non200``        /livez answers 500                    BROKEN
``never-answer``        accepts, NEVER answers                AMBIGUOUS (absent)
``readyz-red``          /livez 200 + /readyz 500              AMBIGUOUS (unready)
``readyz-degraded``     /livez 200 + /readyz 200 but the      AMBIGUOUS (unready)
                        body reports status=degraded
======================  =====================================  ====================

HTTP/1.1 with ``Connection: keep-alive`` is spoken by default — the
launcher's probe issues BOTH GETs over ONE ``/dev/tcp`` fd (no second
connect, no second port race).  ``--http10`` speaks the HTTP/1.0 shape
(the server closes after each response) to exercise the probe's
documented reconnect fallback.

SAFETY
------
* binds 127.0.0.1 only;
* never touches PostgreSQL, never resolves a live-shaped path;
* every request is appended to a JSONL log (the packs assert on the
  OBSERVED probe traffic — e.g. "the probe asked /readyz" — so a
  vacuous pass is impossible);
* the port is chosen by the caller (a free port) — the helper NEVER
  binds a fixed/privileged port.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODE_GREEN = "green"
MODE_WRONG_VERSION = "wrong-version"
MODE_LIVEZ_NON200 = "livez-non200"
MODE_NEVER_ANSWER = "never-answer"
MODE_READYZ_RED = "readyz-red"
MODE_READYZ_DEGRADED = "readyz-degraded"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # injected by the server factory
    mode: str = MODE_GREEN
    version: str = "0.0.0"
    log_path: str | None = None
    http10: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 — silence the default
        pass

    def _record(self, path: str) -> None:
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {"ts": time.time(), "path": path, "mode": self.mode}
                    )
                    + "\n"
                )
        except OSError:
            pass

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.http10:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — http.server's API
        path = self.path.split("?", 1)[0]
        self._record(path)
        if self.mode == MODE_NEVER_ANSWER:
            # accept the connection and NEVER answer — the "no answer at
            # all" class (the probe must time out, not hang).
            time.sleep(3600)
            return
        if path == "/livez":
            if self.mode == MODE_LIVEZ_NON200:
                self._send_json(500, {"status": "broken"})
                return
            self._send_json(
                200,
                {
                    "status": "alive",
                    "uptime_seconds": 1.0,
                    "version": self.version,
                },
            )
            return
        if path == "/readyz":
            if self.mode == MODE_READYZ_RED:
                self._send_json(500, {"status": "degraded"})
                return
            if self.mode == MODE_READYZ_DEGRADED:
                self._send_json(200, {"status": "degraded"})
                return
            self._send_json(200, {"status": "ready"})
            return
        self._send_json(404, {"status": "not-found"})


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: D102
        """Silence the default traceback.

        The probe closes the connection as soon as it has what it needs
        (and the "never-answer" mode deliberately abandons a request) —
        the resulting BrokenPipe traceback is expected noise, not a
        finding."""
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--mode",
        default=MODE_GREEN,
        choices=[
            MODE_GREEN,
            MODE_WRONG_VERSION,
            MODE_LIVEZ_NON200,
            MODE_NEVER_ANSWER,
            MODE_READYZ_RED,
            MODE_READYZ_DEGRADED,
        ],
    )
    parser.add_argument("--version", default="0.0.0")
    parser.add_argument("--log", default=None)
    parser.add_argument(
        "--http10",
        action="store_true",
        help="speak HTTP/1.0 (the server closes after each response)",
    )
    parser.add_argument(
        "--ready-file",
        default=None,
        help="touch this file once the listener is bound",
    )
    args = parser.parse_args()

    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "mode": args.mode,
            "version": args.version,
            "log_path": args.log,
            "http10": args.http10,
        },
    )
    server = _Server(("127.0.0.1", args.port), handler)
    if args.ready_file:
        try:
            with open(args.ready_file, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n")
        except OSError:
            pass
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
