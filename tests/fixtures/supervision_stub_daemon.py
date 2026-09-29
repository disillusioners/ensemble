#!/usr/bin/env python3
"""Stub ensemble daemon for supervision-detection P5 test fixtures.

Serves the health-probe surface the promote/rollback gates and the launcher
expect (/livez + /readyz) with a PINNED version string, and exits cleanly on
SIGTERM (the SINGLE-TERM contract the launcher forwards). Used by
tests/test_supervision_journal.sh (full promote under a fixture ladder) and
tests/test_supervision_e2e.sh (real transient SERVICE boots). Never touches
any real install, port 9797/7979, or any database — bind port and install
dir are always fixture-scoped (ephemeral 18079-family port, /tmp fixture).

Modes:
  default                 — health server only.
  --emit-advisory         — additionally emit the REAL daemon-side boot
                            supervision advisory (upgrade_journal_sweep
                            service hook → python twin classification →
                            `supervision_boot` journal event) once at boot,
                            then keep serving. This exercises the REAL
                            daemon-side twin under whatever cgroup the
                            process was placed in (E2E boot leg).

Usage:
  supervision_stub_daemon.py --port 18079 --version 9.9.9-sbx \
      [--install-dir /tmp/fixture] [--emit-advisory]

Self-contained: stdlib only; --emit-advisory imports the repo's
daemon.tools.upgrade_journal + daemon.services.upgrade_journal_sweep via the
REPO_ROOT inferred from this file's location (tests/fixtures/ → repo root).
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_got_term = False


def _emit_advisory(install_dir: Path) -> str:
    """Run the REAL sweep-service boot hook → return the advisory detail.

    Loads daemon/tools/upgrade_journal.py + daemon/services/
    upgrade_journal_sweep.py DIRECTLY via importlib with a stubbed
    ``daemon.constants`` (the tests/unit/tools/test_promote_cgroup_
    survivorship_python.py exemplar pattern — importing the real
    ``daemon`` package would drag DB dependencies in).
    """
    import importlib.machinery
    import importlib.util
    import types

    m = types.ModuleType("daemon")
    m.__path__ = ["daemon"]
    constants = types.ModuleType("daemon.constants")
    constants.is_reserved_source = lambda x: False  # type: ignore[attr-defined]
    tools = types.ModuleType("daemon.tools")
    tools.__path__ = ["daemon/tools"]
    sys.modules["daemon"] = m
    sys.modules["daemon.constants"] = constants
    sys.modules["daemon.tools"] = tools

    def _load(name: str, path: Path):
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        loader.exec_module(mod)
        return mod

    uj = _load("uj_stub_daemon", REPO_ROOT / "daemon" / "tools" / "upgrade_journal.py")
    sys.modules["daemon.tools.upgrade_journal"] = uj
    sweep = _load(
        "sweep_stub_daemon",
        REPO_ROOT / "daemon" / "services" / "upgrade_journal_sweep.py",
    )

    svc = sweep.UpgradeJournalSweepService(install_dir)
    svc._emit_supervision_boot_advisory()
    det = uj.supervision_detect()
    mapped = uj.supervision_outcome(det.mode, det.state, det.unit)
    return (
        f"state={det.state} mode={det.mode} "
        f"unit={det.unit or '<none>'} outcome={mapped.outcome}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--install-dir", default="")
    ap.add_argument("--emit-advisory", action="store_true")
    # --anchor: an inert cmdline token carrying the anchored binary path
    # (<INSTALL_DIR>/current/ensemble-prod). The stub wrappers exec python
    # directly (so a launcher TERM reaches THIS process), which erases the
    # script path from the cmdline — the anchor keeps the ownership tiers
    # (and the supervision classifier's owned-pid discovery) able to see
    # the process. Purely positional in argv; never otherwise used.
    ap.add_argument("--anchor", default="")
    args = ap.parse_args()

    advisory_detail = ""
    if args.emit_advisory:
        if not args.install_dir:
            print("stub-daemon: --emit-advisory requires --install-dir", file=sys.stderr)
            return 2
        try:
            advisory_detail = _emit_advisory(Path(args.install_dir))
        except Exception as exc:  # noqa: BLE001 — advisory never gates boot
            print(f"stub-daemon: advisory emit failed (continuing): {exc!r}", file=sys.stderr)
    # emit AFTER the server binds? No — emit BEFORE binding so the E2E can
    # poll livez-up as the terminal signal that the advisory already landed.
    if advisory_detail:
        print(f"STUB-ADVISORY: {advisory_detail}", flush=True)

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            if self.path.startswith("/livez"):
                body = json.dumps({"status": "ok", "version": args.version})
                code = 200
            elif self.path.startswith("/readyz"):
                body = json.dumps({"status": "ok", "version": args.version})
                code = 200
            else:
                body = json.dumps({"error": "not found"})
                code = 404
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *a) -> None:  # silence
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)

    def _term(_sig, _frm):
        global _got_term
        _got_term = True
        # shutdown from the handler thread risks deadlock; use the
        # documented serve_forever shutdown via a daemon thread.
        import threading
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    print(f"stub-daemon: serving /livez+/readyz on 127.0.0.1:{args.port} "
          f"version={args.version}", flush=True)
    srv.serve_forever(poll_interval=0.2)
    print("stub-daemon: clean exit (SIGTERM)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
