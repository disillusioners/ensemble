"""Fenced pre-exec launcher for the ENSEMBLE-31 sonic-team dry-run scratch daemon.

LOAD-BEARING — do not launch `python -m daemon` directly.

WHY: `env -i` scrubs environment variables but NOT inherited file
descriptors. The agent bash / proc_run shell is a CHILD of the live
daemon (observed PPID == the live daemon PID), so it inherits the live
daemon's listening-socket fds (fd=6 == the live 9797 listener) because
those fds are not O_CLOEXEC. A scratch daemon launched directly HOLDS
the live listener fd by inheritance without ever binding it — the
wi-2454ede3 port-capture class, via fd inheritance rather than bind.

This launcher closes every fd above stderr and THEN execs the real
daemon, so the daemon starts with only fds 0/1/2 and can hold only
sockets it opens itself.

Usage (environment already fenced by the caller via `env -i ...`):
    <venv>/bin/python <this-file>
"""
import os
import sys

# Close every inherited fd above stderr. C-level, fast, idempotent.
os.closerange(3, 4096)

# Re-exec the daemon as a fresh process: fds 0/1/2 only, env inherited.
os.execv(sys.executable, [sys.executable, "-m", "daemon"])
