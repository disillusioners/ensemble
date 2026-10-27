#!/usr/bin/env python3
"""A FAKE daemon for the soak-gate packs — the scripted /livez + /readyz.

WHY A FAKE
----------
The soak-gate packs must drive the REAL ``gate_soak`` / ``promote.sh``
(lib.sh) against a daemon whose readiness behaviour is SCRIPTED. A real
daemon cannot be told "answer 500 from now on" — but the gate only
cares about the HTTP contract, so a scripted HTTP stub is the honest
substitute for every leg whose subject is the GATE (not the daemon).

The stub is deliberately dumb: it never touches PostgreSQL, never
resolves a live-shaped path, and its only inputs are the mode and the
arguments below. Every request is appended to a JSONL log so the packs
can assert on the OBSERVED probe traffic (e.g. "the soak failed on the
FIRST observation" = exactly one /livez + one /readyz request).

MODES
-----
``green``            /livez 200, /readyz 200 (status "ready").
``hard-red``         /livez 200, /readyz 500 (a genuinely RED component).
``never-readyz``     /livez 200, /readyz HANGS (never answers — a boot
                     that never completes).  The gate's curl times out
                     (rc 28) → the transient class.
``never-all``        the server accepts the connection and NEVER
                     answers anything (livez included).
``grace-transient``  /livez 200, /readyz 503 with a GRACE-ELIGIBLE
                     body (the daemon published an active class-gated
                     grace; every degraded component is grace-eligible).
                     The soak HOLDS the counter here — it never advances.
``flip``             the FIRST /readyz request answers green; every
                     LATER /readyz request is a hard red.  This is the
                     "green → hard-red MID-SOAK" shape: the promote
                     gate's readyz probe takes the green one, the soak's
                     first tick takes the red one.
``version-aware``    the daemon models "the PROMOTED release is broken,
                     the PREVIOUS one is fine": it resolves
                     ``$INSTALL_DIR/current`` → the release dir → its
                     manifest ``binary_version``; that version answers
                     hard-red when it equals ``--bad-version``,
                     otherwise green.  This is what lets the real
                     promote.sh auto-rollback land on a WORKING release
                     (its short re-gate then passes).

SAFETY
------
* binds 127.0.0.1 only;
* the mode never reaches a real database;
* every request is logged (JSONL) for the pack's non-vacuity checks.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: The grace-eligible body the daemon would publish while a
#: class-gated grace is held (requirements §4.2 P2 row 1).
GRACE_BODY = {
    "status": "degraded",
    "components": {
        "database": False,
        "queue_freshness": True,
        "services": True,
        "checkpoint_saver": True,
    },
    "detail": {
        "reasons": ["database: SELECT 1 probe failed or timed out"],
        "component_classes": {
            "database": "probe_timeout",
            "queue_freshness": "ok",
            "services": "ok",
            "checkpoint_saver": "ok",
        },
        "grace": {
            "grace_active": True,
            "grace_reason": "probe_timeout",
            "grace_expires_at": "2099-01-01T00:00:00+00:00",
            "scope": "load",
            "holds": 1,
        },
        "leak_signal": {"detected": False, "checkedout_rising": False, "streak": 0},
    },
}


class State:
    """The stub's mutable state (the mode + the request counters)."""

    def __init__(self, mode: str, version: str, install_dir: str, bad_version: str):
        self.mode = mode
        self.version = version
        self.install_dir = install_dir
        self.bad_version = bad_version
        self.readyz_hits = 0
        self.livez_hits = 0
        self.lock = threading.Lock()

    def current_version(self) -> str:
        """Resolve the SERVING version from the install dir's ``current``.

        ``$INSTALL_DIR/current`` is a symlink to ``releases/<ver>``; the
        manifest inside carries ``binary_version``.  An unreadable
        install dir resolves to the stub's default version (the stub
        never fails closed into "broken" by accident).
        """
        if not self.install_dir:
            return self.version
        current = os.path.join(self.install_dir, "current")
        try:
            target = os.path.realpath(current)
            manifest = os.path.join(target, "manifest.json")
            with open(manifest, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            resolved = data.get("binary_version")
            if resolved:
                return str(resolved)
        except Exception:  # noqa: BLE001 — the fallback is the default
            pass
        return self.version


def _readyz_body(state: State) -> tuple[int, dict]:
    """The (status_code, body) the stub answers for /readyz."""
    mode = state.mode
    with state.lock:
        state.readyz_hits += 1
        hit = state.readyz_hits
    if mode == "green":
        return 200, {
            "status": "ready",
            "components": {
                "database": True,
                "queue_freshness": True,
                "services": True,
                "checkpoint_saver": True,
            },
            "detail": {"reasons": [], "inflight_turns": 0},
        }
    if mode == "hard-red":
        return 500, {
            "status": "degraded",
            "components": {
                "database": False,
                "queue_freshness": True,
                "services": True,
                "checkpoint_saver": True,
            },
            "detail": {
                "reasons": ["database: SELECT 1 failed with an explicit error"],
                "component_classes": {
                    "database": "hard_component_red",
                    "queue_freshness": "ok",
                    "services": "ok",
                    "checkpoint_saver": "ok",
                },
                "grace": {"grace_active": False},
                "leak_signal": {"detected": False, "checkedout_rising": False, "streak": 0},
            },
        }
    if mode == "grace-transient":
        return 503, GRACE_BODY
    if mode == "flip":
        # The FIRST request is green (the promote gate consumes it);
        # every later request is a hard red (the soak's first tick).
        if hit == 1:
            return 200, {
                "status": "ready",
                "components": {
                    "database": True,
                    "queue_freshness": True,
                    "services": True,
                    "checkpoint_saver": True,
                },
                "detail": {"reasons": [], "inflight_turns": 0},
            }
        return 500, {"status": "degraded", "detail": {"reasons": ["database: hard red"]}}
    if mode == "version-flip":
        # The PROMOTED release is broken AND it breaks MID-SOAK: its
        # FIRST /readyz request is green (the promote gate consumes it)
        # and every later request is a hard red (the soak's first tick).
        # The ROLLED-BACK release (whatever ``current`` points at after
        # the rollback) always answers green — so the promote's short
        # re-gate passes and the rollback completes.
        if state.bad_version and state.current_version() != state.bad_version:
            return 200, {
                "status": "ready",
                "components": {
                    "database": True,
                    "queue_freshness": True,
                    "services": True,
                    "checkpoint_saver": True,
                },
                "detail": {"reasons": [], "inflight_turns": 0},
            }
        if hit == 1:
            return 200, {
                "status": "ready",
                "components": {
                    "database": True,
                    "queue_freshness": True,
                    "services": True,
                    "checkpoint_saver": True,
                },
                "detail": {"reasons": [], "inflight_turns": 0},
            }
        return 500, {"status": "degraded", "detail": {"reasons": ["database: hard red"]}}
    if mode == "version-aware":
        if state.bad_version and state.current_version() == state.bad_version:
            return 500, {
                "status": "degraded",
                "components": {
                    "database": False,
                    "queue_freshness": True,
                    "services": True,
                    "checkpoint_saver": True,
                },
                "detail": {
                    "reasons": ["database: the promoted release is broken"],
                    "component_classes": {
                        "database": "hard_component_red",
                        "queue_freshness": "ok",
                        "services": "ok",
                        "checkpoint_saver": "ok",
                    },
                    "grace": {"grace_active": False},
                },
            }
        return 200, {
            "status": "ready",
            "components": {
                "database": True,
                "queue_freshness": True,
                "services": True,
                "checkpoint_saver": True,
            },
            "detail": {"reasons": [], "inflight_turns": 0},
        }
    # never-readyz is handled by the handler (it hangs).
    return 200, {"status": "ready", "detail": {"reasons": []}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--version", default="0.0.0-sbx")
    ap.add_argument("--install-dir", default="")
    ap.add_argument("--bad-version", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--ready-file", default="")
    args = ap.parse_args()

    state = State(args.mode, args.version, args.install_dir, args.bad_version)
    log_lock = threading.Lock()

    def record(path: str) -> None:
        if not args.log:
            return
        line = json.dumps({"path": path, "ts": time.time()}) + "\n"
        with log_lock:
            with open(args.log, "a", encoding="utf-8") as fh:
                fh.write(line)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 — http.server's API
            record(self.path)
            if state.mode in ("never-all", "never-readyz") and (
                state.mode == "never-all" or self.path == "/readyz"
            ):
                # A boot that never completes: the connection is
                # accepted and NO response is ever written.
                time.sleep(600)
                return
            if self.path == "/livez":
                with state.lock:
                    state.livez_hits += 1
                version = state.current_version()
                self._send(
                    200,
                    {
                        "status": "alive",
                        "uptime_seconds": 1.0,
                        "version": version,
                    },
                )
                return
            if self.path == "/readyz":
                code, payload = _readyz_body(state)
                self._send(code, payload)
                return
            self._send(404, {"status": "not found"})

        def log_message(self, *args):  # noqa: D102 — silence stderr spam
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    if args.ready_file:
        with open(args.ready_file, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"port": args.port, "mode": args.mode, "pid": os.getpid()}))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
