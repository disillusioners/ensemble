#!/usr/bin/env python3
# ============================================================================
# test/drills/p21_stub_daemon.py — "ensemble-prod" TEST DOUBLE (P2.1 drills)
# ============================================================================
# A small standalone daemon that mimics the two probe endpoints the upgrade
# pipeline gates on, with drill-selectable behavior baked in per release
# (the drill generator writes STUB_VERSION / STUB_MODE below):
#
#   /livez   200 {"status":"alive","uptime_seconds":N,"version":STUB_VERSION}
#   /readyz  200 {"status":"ready","reasons":[]}
#            503 {"status":"degraded","reasons":["forced-degraded (drill)"]}
#            in mode=ready503 (liveness stays 200 — probes are independent)
#
# Modes:
#   serve    normal: serves STUB_VERSION on /livez (green everything)
#   wrongver serves STUB_VERSION + "-WRONG" → promote version-verify fails
#   exit78   exits 78 at boot (fatal-config shape; the launcher must NOT
#            restart-loop on it)
#   ready503 /readyz serves 503 forever (degraded daemon shape)
#
# SIGTERM → exit 0 (graceful single-term shape).
# Port comes from the environment (the launcher exports INSTALL_DIR/.env).
# Real PyInstaller builds are NEVER used in drills — that is T10's wave.
# ============================================================================
import http.server
import json
import os
import signal
import sys

# ── baked by the drill generator (sed placeholders) ─────────────────────────
STUB_VERSION = "0.0.0-stub"
STUB_MODE = "serve"
# ───────────────────────────────────────────────────────────────────────────

PORT = int(os.environ.get("PORT", "8080"))

if STUB_MODE == "exit78":
    print("stub-daemon: fatal config (drill-induced) — exiting 78", file=sys.stderr)
    sys.exit(78)

SERVE_VERSION = STUB_VERSION + "-WRONG" if STUB_MODE == "wrongver" else STUB_VERSION


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/livez":
            self._json(200, {"status": "alive", "uptime_seconds": 1,
                             "version": SERVE_VERSION})
        elif self.path == "/readyz":
            if STUB_MODE == "ready503":
                self._json(503, {"status": "degraded",
                                 "reasons": ["forced-degraded (drill)"]})
            else:
                self._json(200, {"status": "ready", "reasons": []})
        else:
            self._json(404, {})

    def log_message(self, *args):
        pass


signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

http.server.HTTPServer.allow_reuse_address = True
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
