#!/usr/bin/env python3
"""E2E browser automation test for the VSCode crash-fix changes.

Verifies end-to-end (Playwright + HTTP) that:

1. The ``PUT /api/settings/editor {editor: vscode}`` lazy-starts code-server
   and the browser can load the IDE via ``GET /vscode`` (not a 5xx).
2. When code-server is stopped, ``GET /vscode`` returns ``503`` with a
   ``Retry-After`` header (clean proxy error, not a 500).
3. When code-server is killed mid-session, the ``_adopted_watchdog_loop``
   (or the equivalent ``_watchdog_loop`` for fresh spawns) detects the
   death and the proxy returns to a healthy state without leaking 500s.

Self-contained: spawns the dev server if port 8079 is free, restores
state, and tears down all spawned processes. Hard 5-minute cap via
SIGALRM. Screenshots are written to ``test/packs/vscode_e2e_screenshots/``.

Run as a test pack:

    .venv/bin/python test/packs/vscode_e2e_browser_test.py

Or via the included shell wrapper:

    bash test/packs/vscode_e2e_browser_test.sh
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import requests
from playwright.sync_api import Browser, Page, sync_playwright

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

BASE_URL = "http://localhost:8079"
HEALTH_URL = f"{BASE_URL}/api/health"
DOCS_URL = f"{BASE_URL}/docs"
EDITOR_PUT_URL = f"{BASE_URL}/api/settings/editor"
EDITOR_STATUS_URL = f"{BASE_URL}/api/settings/editor/status"
VSCODE_STOP_URL = f"{BASE_URL}/api/settings/vscode/stop"
# Use a trailing slash — FastAPI mounts require /vscode/ to reach the
# sub-app (a bare /vscode hits the main app's catch-all, which returns 404).
VSCODE_URL = f"{BASE_URL}/vscode/"

# 5-minute hard cap (matches the test-pack skill invariant).
PACK_TIMEOUT_S = 300

# Dev-server readiness and code-server startup budgets.
DEV_READY_TIMEOUT_S = 60
CODE_SERVER_READY_TIMEOUT_S = 45
CRASH_RECOVERY_TIMEOUT_S = 60

# Per-step HTTP timeouts.
HTTP_TIMEOUT_S = 10
LONG_HTTP_TIMEOUT_S = 30

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_SCRIPT = PROJECT_ROOT / "dev.sh"
DATA_DIR = PROJECT_ROOT / "data_dev"
PID_FILE = DATA_DIR / "vscode-server.pid"
DAEMON_LOG = Path("/tmp/vscode_e2e_browser_test_daemon.log")
SCREENSHOT_DIR = Path(__file__).resolve().parent / "vscode_e2e_screenshots"

# Port 8088 is the ensemble self-system — must never be touched.
FORBIDDEN_PORTS = {8088}


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

# Per-scenario result: list of (label, status, evidence).
SCENARIOS: list[tuple[str, str, str, str]] = []
# Screenshots saved during the run: list of (label, path).
SCREENSHOTS: list[tuple[str, str]] = []
# Environment notes observed during pre-checks.
ENV_NOTES: list[str] = []

DEV_PID: int | None = None
DEV_STARTED_BY_TEST = False
PLAYWRIGHT = None
BROWSER: Browser | None = None
EDITOR_RESTORED_TO_BUILTIN = False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def log(msg: str) -> None:
    """Timestamped log line — always flushed so progress is visible."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _assert_port_safe(port: int) -> None:
    """Refuse to operate on a forbidden port."""
    if port in FORBIDDEN_PORTS:
        raise RuntimeError(f"Refusing to touch forbidden port {port}")


def _port_listening(port: int) -> int | None:
    """Return the PID listening on ``port`` (TCP/LISTEN) or ``None``.

    Read-only — no port is touched. The forbidden-port guard lives in
    the *mutating* helpers (``_stop_daemon_if_started``, ``_kill_our_code_server``).
    """
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    pid_str = out.stdout.strip().splitlines()[0]
    try:
        return int(pid_str)
    except ValueError:
        return None


def _is_managed_code_server(pid: int) -> bool:
    """Return True iff ``pid`` is a code-server we started (our PID file)."""
    if not PID_FILE.exists():
        return False
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("pid") == pid


def _read_pid_file() -> dict | None:
    if not PID_FILE.exists():
        return None
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _wait_for_daemon_ready(timeout_s: int) -> bool:
    """Poll ``/api/health`` until the daemon answers (or the budget elapses)."""
    deadline = time.monotonic() + timeout_s
    last_exc: str | None = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(HEALTH_URL, timeout=HTTP_TIMEOUT_S)
        except requests.RequestException as exc:
            last_exc = f"{type(exc).__name__}: {exc}"
        else:
            if r.status_code < 500:
                log(f"Daemon ready (status={r.status_code})")
                return True
            last_exc = f"status={r.status_code}"
        time.sleep(1.0)
    log(f"Daemon NOT ready within {timeout_s}s ({last_exc})")
    return False


def _wait_for_vscode_status(target: str, timeout_s: int) -> dict:
    """Poll ``/api/settings/editor/status`` until ``status == target``."""
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            r = requests.get(EDITOR_STATUS_URL, timeout=HTTP_TIMEOUT_S)
            last = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            if r.status_code == 200 and last.get("status") == target:
                log(f"code-server status reached: {target}")
                return last
        time.sleep(1.0)
    log(f"code-server did NOT reach status={target} in {timeout_s}s; last={last}")
    return last


def _put_editor(value: str, timeout_s: int = LONG_HTTP_TIMEOUT_S) -> tuple[int, dict | str]:
    """PUT ``/api/settings/editor`` and return (status_code, parsed_body_or_text)."""
    try:
        r = requests.put(
            EDITOR_PUT_URL,
            json={"editor": value},
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        return 0, f"{type(exc).__name__}: {exc}"
    body: dict | str
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


def _post_vscode_stop(timeout_s: int = LONG_HTTP_TIMEOUT_S) -> tuple[int, dict | str]:
    """POST ``/api/settings/vscode/stop`` (direct stop path, no editor dance).

    Cleaner than PUT editor=builtin because it doesn't have the
    user-stopped restart race: stop() always works regardless of
    editor preference state.
    """
    try:
        r = requests.post(VSCODE_STOP_URL, timeout=timeout_s)
    except requests.RequestException as exc:
        return 0, f"{type(exc).__name__}: {exc}"
    body: dict | str
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


def _start_daemon_if_needed() -> bool:
    """Start ``./dev.sh`` if port 8079 is free. Return True if we started it."""
    global DEV_PID, DEV_STARTED_BY_TEST
    if _port_listening(8079) is not None:
        log("Port 8079 already in use — using the existing daemon")
        DEV_STARTED_BY_TEST = False
        return False
    log(f"Starting dev server: {DEV_SCRIPT}")
    log_file = DAEMON_LOG.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["bash", str(DEV_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    DEV_PID = proc.pid
    DEV_STARTED_BY_TEST = True
    log(f"Dev server started (pid={DEV_PID}, log={DAEMON_LOG})")
    return True


def _stop_daemon_if_started() -> None:
    """Stop the dev server we started.

    uvicorn ``--reload`` uses a reloader that forks a worker which can
    outlive the parent shell (the worker gets re-spawned by the
    reloader). We therefore don't bother with graceful shutdown — for a
    test, we SIGKILL anything bound to 8079 plus the bash parent we
    spawned, repeating until the port is free. The
    ``_assert_port_safe(8079)`` guard ensures we never touch a port we
    don't own.
    """
    if not DEV_STARTED_BY_TEST:
        return
    _assert_port_safe(8079)  # guard — only our dev port is safe to tear down

    def _all_pids_on_port(port: int) -> list[int]:
        """All PIDs in any way connected to ``port`` (LISTEN + ESTABLISHED)."""
        try:
            out = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-t"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return []
        pids: set[int] = set()
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.add(int(line))
            except ValueError:
                pass
        return sorted(pids)

    def _pgrep_children(root_pid: int) -> list[int]:
        """Return descendants of ``root_pid`` via ``pgrep -P`` (recursive)."""
        result: set[int] = set()
        stack = [root_pid]
        while stack:
            parent = stack.pop()
            try:
                out = subprocess.run(
                    ["pgrep", "-P", str(parent)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                continue
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    pid = int(line)
                    if pid not in result:
                        result.add(pid)
                        stack.append(pid)
                except ValueError:
                    pass
        return sorted(result)

    log(f"Stopping dev server (root pid={DEV_PID})")
    # 1) Kill the whole descendant tree of the bash parent.
    if DEV_PID is not None and Path(f"/proc/{DEV_PID}").exists():
        descendants = _pgrep_children(DEV_PID) + [DEV_PID]
        log(f"  descendent pids to kill: {descendants}")
        for pid in sorted(descendants, reverse=True):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    # 2) Anything still on 8079: SIGKILL on every iteration until the
    #    port is free (handles re-spawn by the reloader).
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        pids = _all_pids_on_port(8079)
        if not pids:
            log("Dev server fully stopped; port 8079 free")
            return
        log(f"  pids still on 8079: {pids}; sending SIGKILL")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        time.sleep(0.5)
    log(f"WARNING: port 8079 still busy after cleanup: {_all_pids_on_port(8079)}")


def _ensure_editor_builtin() -> None:
    """Best-effort: stop code-server via the dedicated endpoint."""
    global EDITOR_RESTORED_TO_BUILTIN
    if EDITOR_RESTORED_TO_BUILTIN:
        return
    if _port_listening(8079) is None:
        return
    try:
        status, body = _post_vscode_stop(timeout_s=15)
        log(f"Stopped code-server via POST /vscode/stop (status={status}, body={body})")
        EDITOR_RESTORED_TO_BUILTIN = True
    except Exception as exc:
        log(f"WARNING: could not stop code-server: {exc!r}")


def _kill_our_code_server() -> None:
    """SIGKILL the code-server we started, if any. Never touches strangers."""
    info = _read_pid_file()
    if not info:
        return
    pid = info.get("pid")
    if not isinstance(pid, int):
        return
    if not _is_managed_code_server(pid):
        return
    # Final sanity: the target must be a descendant of our dev server, not a
    # system service on a forbidden port. We do not operate on 8088.
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace")
    except OSError:
        cmdline = ""
    if "code-server" not in cmdline:
        log(f"  refusing to kill pid={pid}: cmdline lacks 'code-server' ({cmdline!r})")
        return
    log(f"Killing our code-server pid={pid} (from {PID_FILE})")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        log(f"WARNING: failed to kill code-server pid={pid}: {exc!r}")
    # Wait for the port to free up
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)


def _take_screenshot(page: Page, label: str) -> str | None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in label)
    path = SCREENSHOT_DIR / f"{int(time.time())}_{safe}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:
        log(f"WARNING: screenshot failed ({label}): {exc!r}")
        return None
    log(f"Screenshot saved: {path}")
    SCREENSHOTS.append((label, str(path)))
    return str(path)


def _record(scenario: str, status: str, evidence: str) -> None:
    """Append (scenario, status, evidence) and keep an emoji summary."""
    SCENARIOS.append((scenario, status, evidence, ""))
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⤼"}.get(status, "?")
    log(f"  {icon} {scenario}: {status} — {evidence}")


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


def scenario_1_browser_loads_vscode() -> None:
    """Browser navigates to /vscode and the proxy returns a 2xx/3xx (not 5xx)."""
    log("SCENARIO 1: browser navigates to /vscode and gets HTML (not 5xx)")
    if BROWSER is None:
        _record("1_browser_loads_vscode", "SKIP", "no browser available")
        return
    try:
        # 1a. Editor PUT to lazy-start code-server.
        status, body = _put_editor("vscode", timeout_s=CODE_SERVER_READY_TIMEOUT_S + 5)
        if status != 200:
            _record("1_browser_loads_vscode", "FAIL", f"PUT editor=vscode returned {status}: {body}")
            return
        # 1b. Wait for status=running.
        state = _wait_for_vscode_status("running", CODE_SERVER_READY_TIMEOUT_S)
        if state.get("status") != "running":
            _record("1_browser_loads_vscode", "FAIL", f"code-server never reached running: {state}")
            return
        # 1c. Verify PID file was written (sanity check).
        pid_info = _read_pid_file()
        if not pid_info or "pid" not in pid_info:
            _record("1_browser_loads_vscode", "FAIL", f"PID file missing or empty: {PID_FILE}")
            return
        log(f"PID file: {pid_info}")
        # 1d. Browser navigation. We use /vscode/ and also test /vscode/healthz
        # (a direct proxy→code-server request with no folder to validate).
        context = BROWSER.new_context()
        try:
            page = context.new_page()
            console_errors: list[str] = []
            page.on(
                "console",
                lambda msg: console_errors.append(f"{msg.type}: {msg.text}")
                if msg.type == "error" else None,
            )
            # Direct test: /vscode/healthz (no folder validation; code-server
            # returns 200 OK with "OK" body). If this returns 200, the proxy
            # is reaching code-server successfully.
            r_health = requests.get(
                f"{BASE_URL}/vscode/healthz",
                timeout=HTTP_TIMEOUT_S,
                allow_redirects=False,
            )
            log(
                f"GET /vscode/healthz → status={r_health.status_code} "
                f"body={r_health.text[:60]!r}"
            )
            # Browser navigation to /vscode/ — code-server may 302 to a folder
            # URL; the proxy will then validate that folder. We accept any
            # response that is NOT a 5xx.
            response = page.goto(VSCODE_URL, wait_until="domcontentloaded", timeout=20000)
            status_code = response.status if response is not None else 0
            final_url = response.url if response is not None else "?"
            log(f"Browser response: status={status_code}, final_url={final_url}")
            # Take screenshot regardless of status — useful for debugging.
            _take_screenshot(page, "scenario1_vscode_load")
            # Pull the body and check for code-server markers.
            content = (page.content() or "").lower()
            title = page.title() or ""
            log(f"Page title: {title!r}, body length={len(content)}")
            looks_like_vscode = any(
                marker in content
                for marker in ("code-server", "vscode", "workbench", "monaco", "out/vs")
            )
            health_ok = r_health.status_code == 200
            evidence = (
                f"status={status_code} title={title!r} "
                f"healthz={r_health.status_code} final_url={final_url} "
                f"vscode_markers={looks_like_vscode} console_errors={len(console_errors)}"
            )
            # The proxy is healthy iff:
            #  - The browser request is not a 5xx, AND
            #  - The /vscode/healthz direct probe returns 2xx
            if status_code < 500 and health_ok:
                _record("1_browser_loads_vscode", "PASS", evidence)
            elif status_code < 500 and not health_ok:
                _record(
                    "1_browser_loads_vscode",
                    "FAIL",
                    evidence + " (proxy did not forward /healthz to code-server)",
                )
            else:
                _record("1_browser_loads_vscode", "FAIL", evidence + " (5xx regression)")
        finally:
            context.close()
    except Exception as exc:
        _record("1_browser_loads_vscode", "FAIL", f"exception: {exc!r}\n{traceback.format_exc()}")


def scenario_2_503_retry_after_when_down() -> None:
    """When code-server is stopped, /vscode returns 503 + Retry-After."""
    log("SCENARIO 2: GET /vscode returns 503 + Retry-After when code-server is down")
    if _port_listening(8079) is None:
        _record("2_503_when_down", "SKIP", "daemon not listening")
        return
    try:
        # Stop the code-server via POST /api/settings/vscode/stop. This
        # is cleaner than PUT editor=builtin because stop() is the
        # single-purpose endpoint — no user-stopped restart race.
        status, body = _post_vscode_stop(timeout_s=15)
        if status != 200:
            _record("2_503_when_down", "FAIL", f"POST /vscode/stop returned {status}: {body}")
            return
        # Confirm status is no longer running.
        st = _wait_for_vscode_status("stopped", timeout_s=10)
        if st.get("status") not in ("stopped",):
            log(f"WARNING: code-server not 'stopped' after stop POST: {st}")
        # Now make a raw HTTP request and assert the response.
        try:
            r = requests.get(VSCODE_URL, timeout=HTTP_TIMEOUT_S, allow_redirects=False)
        except requests.RequestException as exc:
            _record("2_503_when_down", "FAIL", f"HTTP request raised: {exc!r}")
            return
        retry_after = r.headers.get("Retry-After")
        evidence = (
            f"status={r.status_code} retry_after={retry_after!r} "
            f"content_type={r.headers.get('content-type')!r} body[:200]={r.text[:200]!r}"
        )
        if r.status_code == 503 and retry_after is not None:
            _record("2_503_when_down", "PASS", evidence)
        elif r.status_code == 503:
            _record("2_503_when_down", "FAIL", evidence + " (missing Retry-After header)")
        else:
            _record("2_503_when_down", "FAIL", evidence)
    except Exception as exc:
        _record("2_503_when_down", "FAIL", f"exception: {exc!r}")


def scenario_3_crash_recovery() -> None:
    """Killing code-server triggers the watchdog restart; /vscode recovers."""
    log("SCENARIO 3: SIGKILL code-server, watchdog restarts, /vscode recovers")
    if _port_listening(8079) is None:
        _record("3_crash_recovery", "SKIP", "daemon not listening")
        return
    try:
        # Re-assert editor=vscode in case the manager was stopped
        # (Scenario 1 left it running, so this is typically a no-op).
        status, body = _put_editor("vscode", timeout_s=CODE_SERVER_READY_TIMEOUT_S + 5)
        if status != 200:
            _record("3_crash_recovery", "FAIL", f"PUT editor=vscode returned {status}: {body}")
            return
        st = _wait_for_vscode_status("running", CODE_SERVER_READY_TIMEOUT_S)
        if st.get("status") != "running":
            _record("3_crash_recovery", "FAIL", f"code-server never reached running: {st}")
            return
        # Read the PID we're about to kill.
        pid_info = _read_pid_file()
        if not pid_info or "pid" not in pid_info:
            _record("3_crash_recovery", "FAIL", f"PID file missing: {PID_FILE}")
            return
        target_pid = int(pid_info["pid"])
        log(f"Killing managed code-server pid={target_pid} with SIGKILL")
        try:
            os.kill(target_pid, signal.SIGKILL)
        except ProcessLookupError:
            log("code-server already gone before SIGKILL")
        except OSError as exc:
            _record("3_crash_recovery", "FAIL", f"SIGKILL failed: {exc!r}")
            return
        # Wait for the kill to take effect (PID gone). Without this, the
        # polling loop below can catch a stale 302 from the dying
        # process and report a false-positive recovery.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(target_pid, 0)
            except ProcessLookupError:
                log(f"PID {target_pid} confirmed dead; beginning recovery poll")
                break
            time.sleep(0.1)
        else:
            log(f"WARNING: pid {target_pid} still alive after 5s — proceeding anyway")
        # Poll /vscode (NOT via browser) for the recovery. We track every
        # response so we can confirm no 5xx sneaks through and that the
        # recovery was real (not a stale response from the dying process).
        deadline = time.monotonic() + CRASH_RECOVERY_TIMEOUT_S
        seen_statuses: list[int] = []
        seen_503 = False
        seen_503_with_retry = False
        seen_live = False  # any 2xx OR 3xx (code-server 302 redirect counts as live)
        final_status: int | None = None
        while time.monotonic() < deadline:
            try:
                r = requests.get(VSCODE_URL, timeout=HTTP_TIMEOUT_S, allow_redirects=False)
            except requests.RequestException as exc:
                seen_statuses.append(-1)
                log(f"  poll: request error: {exc!r}")
            else:
                seen_statuses.append(r.status_code)
                if r.status_code == 503:
                    seen_503 = True
                    if r.headers.get("Retry-After") is not None:
                        seen_503_with_retry = True
                if 200 <= r.status_code < 400:
                    seen_live = True
                    final_status = r.status_code
                    break
                # If the proxy returned 500, that's a regression.
                if r.status_code == 500:
                    final_status = r.status_code
                    break
            time.sleep(1.0)
        # Verify the NEW code-server (post-restart) is alive by reading
        # the PID file — its pid must differ from the one we killed.
        new_pid_info = _read_pid_file()
        new_pid = new_pid_info.get("pid") if new_pid_info else None
        pid_rotated = new_pid is not None and new_pid != target_pid
        evidence = (
            f"polled_statuses={seen_statuses[:20]}{'...' if len(seen_statuses) > 20 else ''} "
            f"saw_503={seen_503} 503_with_retry={seen_503_with_retry} "
            f"recovered_2xx_or_3xx={seen_live} final_status={final_status} "
            f"total_polls={len(seen_statuses)} old_pid={target_pid} new_pid={new_pid} "
            f"pid_rotated={pid_rotated}"
        )
        # Pass criteria:
        #  - The proxy returned 503 at least once during the death/restart
        #    window (proves the readiness check correctly observed the death)
        #  - Then the proxy returned a non-5xx (proves recovery)
        #  - The PID file rotated (proves the manager spawned a new process)
        #  - No 500 leaked into the polls
        if (
            seen_live
            and seen_503
            and pid_rotated
            and 500 not in seen_statuses
            and final_status != 500
        ):
            _record("3_crash_recovery", "PASS", evidence)
        elif final_status == 500:
            _record("3_crash_recovery", "FAIL", evidence + " (regression: 500 observed)")
        elif not seen_live:
            _record("3_crash_recovery", "FAIL", evidence + " (never recovered to non-5xx)")
        elif not seen_503:
            _record(
                "3_crash_recovery",
                "FAIL",
                evidence + " (never saw 503 during death window — readiness check may be broken)",
            )
        elif not pid_rotated:
            _record(
                "3_crash_recovery",
                "FAIL",
                evidence + " (PID did not rotate — watchdog may not have restarted)",
            )
        else:
            _record("3_crash_recovery", "FAIL", evidence)
    except Exception as exc:
        _record("3_crash_recovery", "FAIL", f"exception: {exc!r}\n{traceback.format_exc()}")


# --------------------------------------------------------------------------- #
# Pre-checks
# --------------------------------------------------------------------------- #


def precheck_environment() -> bool:
    """Sanity-check the environment. Add findings to ``ENV_NOTES``."""
    log("PRE-CHECK: environment")
    # Playwright already verified to import; capture version.
    try:
        from importlib.metadata import version as _v
        ENV_NOTES.append(f"playwright={_v('playwright')}")
    except Exception:
        pass
    # code-server binary
    try:
        out = subprocess.run(
            ["code-server", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            ENV_NOTES.append(f"code-server={out.stdout.strip().splitlines()[0]}")
        else:
            ENV_NOTES.append(f"code-server probe failed: rc={out.returncode}")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        ENV_NOTES.append(f"code-server NOT installed: {exc!r}")
    # Port 8079 status
    if _port_listening(8079) is not None:
        ENV_NOTES.append("port 8079: in use at pre-check")
    else:
        ENV_NOTES.append("port 8079: free at pre-check")
    # Port 8088 status (forbidden)
    if _port_listening(8088) is not None:
        ENV_NOTES.append("port 8088: in use (forbidden — must not touch)")
    # Database
    if (PROJECT_ROOT / ".env").exists():
        env_text = (PROJECT_ROOT / ".env").read_text(encoding="utf-8", errors="replace")
        if "POSTGRES_HOST" in env_text and "POSTGRES_DB" in env_text:
            ENV_NOTES.append("DB: PostgreSQL (.env has POSTGRES_HOST/DB)")
        elif "SQLITE" in env_text.upper() or "sqlite" in env_text:
            ENV_NOTES.append("DB: SQLite (warning — project prefers PostgreSQL)")
        else:
            ENV_NOTES.append("DB: unknown (.env present, no POSTGRES markers found)")
    else:
        ENV_NOTES.append("DB: no .env file")
    for note in ENV_NOTES:
        log(f"  env: {note}")
    # Hard requirements
    if "NOT installed" in next((n for n in ENV_NOTES if "code-server=" in n), ""):
        log("  ! code-server NOT installed — some scenarios may be limited")
    return True


# --------------------------------------------------------------------------- #
# Teardown
# --------------------------------------------------------------------------- #


def teardown() -> None:
    log("TEARDOWN")
    # 1. Browser
    global BROWSER, PLAYWRIGHT
    if BROWSER is not None:
        try:
            BROWSER.close()
        except Exception:
            pass
        BROWSER = None
    if PLAYWRIGHT is not None:
        try:
            PLAYWRIGHT.stop()
        except Exception:
            pass
        PLAYWRIGHT = None
    # 2. Editor preference
    _ensure_editor_builtin()
    # 3. Our code-server (if any still running)
    _kill_our_code_server()
    # 4. Dev server (only if we started it)
    _stop_daemon_if_started()
    # 5. Confirm port 8079 is free
    if _port_listening(8079) is None:
        log("  port 8079: free")
    else:
        log("  port 8079: STILL in use (manual cleanup may be required)")
    # 6. Confirm we did NOT touch 8088
    if _port_listening(8088) is not None:
        log("  port 8088: still in use (untouched by test)")


# --------------------------------------------------------------------------- #
# Timeout
# --------------------------------------------------------------------------- #


class _TimeoutError(Exception):
    pass


def _install_alarm() -> None:
    def _raise(_signum, _frame):
        raise _TimeoutError(f"pack exceeded {PACK_TIMEOUT_S}s hard cap")

    signal.signal(signal.SIGALRM, _raise)
    signal.alarm(PACK_TIMEOUT_S)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    global PLAYWRIGHT, BROWSER
    started = time.monotonic()
    _install_alarm()
    atexit.register(teardown)
    exit_code = 0
    try:
        precheck_environment()
        if not _start_daemon_if_needed():
            log("Using existing daemon on 8079")
        if not _wait_for_daemon_ready(DEV_READY_TIMEOUT_S):
            log("FATAL: daemon not ready — aborting")
            return 1
        # Sanity: /docs reachable
        try:
            r = requests.get(DOCS_URL, timeout=HTTP_TIMEOUT_S)
            log(f"GET /docs → status={r.status_code}")
        except requests.RequestException as exc:
            log(f"WARNING: /docs probe failed: {exc!r}")
        # Start Playwright
        try:
            PLAYWRIGHT = sync_playwright().start()
            BROWSER = PLAYWRIGHT.chromium.launch(headless=True)
            log("Playwright Chromium launched (headless)")
        except Exception as exc:
            log(f"FATAL: Playwright launch failed: {exc!r}")
            return 1
        # Run scenarios in order. Scenario 3 (crash recovery) runs before
        # Scenario 2 (503 after stop) because Scenario 2 calls
        # ``manager.stop()`` which sets ``user_stopped=True``; once that
        # flag is set, a subsequent ``start()`` refuses to spawn a new
        # process (documented in vscode_server_manager.py:219-229), so the
        # SIGKILL in Scenario 3 would have no code-server left to recover.
        scenario_1_browser_loads_vscode()
        scenario_3_crash_recovery()
        scenario_2_503_retry_after_when_down()
    except _TimeoutError as exc:
        log(f"TIMEOUT: {exc}")
        exit_code = 124
    except Exception as exc:
        log(f"UNCAUGHT: {exc!r}\n{traceback.format_exc()}")
        exit_code = 1
    finally:
        runtime = time.monotonic() - started
        log(f"Total runtime: {runtime:.1f}s")
        # Build the formal pack report.
        print()
        print("=" * 64)
        print("=== Test Pack: vscode_e2e_browser ===")
        for scenario, status, evidence, _ in SCENARIOS:
            print(f"  {scenario}: {status}")
            if status != "PASS":
                print(f"    evidence: {evidence}")
        for label, path in SCREENSHOTS:
            print(f"  screenshot [{label}]: {path}")
        for note in ENV_NOTES:
            print(f"  env: {note}")
        # If we hit a hard timeout, that trumps the per-scenario result.
        if exit_code == 124:
            result = "TIMEOUT"
        else:
            statuses = {s for _, s, _, _ in SCENARIOS}
            if "FAIL" in statuses:
                result = "FAIL"
            elif not statuses:
                result = "FAIL"  # no scenarios ran → treat as failure
            elif statuses == {"SKIP"}:
                result = "SKIP"
            elif statuses == {"PASS"}:
                result = "PASS"
            elif statuses.issubset({"PASS", "SKIP"}):
                result = "PASS"
            else:
                result = "PARTIAL"
        print(f"RESULT: {result}")
        print("=" * 64)
        # If a hard exit code was already set (124 / 1), keep it.
        if exit_code == 0:
            exit_code = {"PASS": 0, "FAIL": 1, "PARTIAL": 2, "SKIP": 3, "TIMEOUT": 124}[result]
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
