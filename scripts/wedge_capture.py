#!/usr/bin/env python3
"""The wedge-capture OPERATOR CLI — a THIN client over the admin API.

This script speaks ONLY HTTP to the daemon's ``/api/wedge-captures``
surface. It opens NO database connection, reads NO capture directory
directly, and re-implements NO parsing logic — the API owns the
normalization (and the filesystem fallback when the table is
unreachable).

It is STDLIB-ONLY on purpose: it must run on a host with no venv.

Subcommands
-----------
``list``      the capture list (the default)
``get <id>``  one capture's payload summary
``capture``   SUBMIT a manual capture — prints the 202's pre-allocated
              id + directory. It does NOT wait for the capture to
              finish: completion is read through ``list`` / ``get``.
``status``    the watchdog status + the effective auto-continue mode

Exit codes
----------
0 ok · 1 not-found or typed refusal (capture_busy /
manual_rate_limited / watchdog_disabled — token printed verbatim) ·
2 usage · 3 transport error

The three typed refusals (``capture_busy``, ``manual_rate_limited``,
``watchdog_disabled``) are surfaced VERBATIM with their house token.

THE PORT IS NEVER HARD-CODED (D35)
-----------------------------------
``--base-url`` wins; else ``ENSEMBLE_DAEMON_URL`` / ``DAEMON_PORT`` /
``PORT`` from the environment; else ``http://127.0.0.1:9797``.

``INSTALL_DIR`` is EXPLICIT (``--install-dir``, default from the
``INSTALL_DIR`` env var) and is used ONLY to locate the release's
``current`` symlink for a version display — never to read or write
capture data. The house rule stands: an operator script NEVER derives
an install path from ``$HOME``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

#: The default base URL — the last resort, after the env chain.
DEFAULT_BASE_URL = "http://127.0.0.1:9797"

#: The API prefix.
API_PREFIX = "/api/wedge-captures"

#: Exit codes.
EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2
EXIT_TRANSPORT = 3

#: The typed refusal tokens this CLI surfaces verbatim.
REFUSAL_TOKENS = ("capture_busy", "manual_rate_limited", "watchdog_disabled")


def resolve_base_url(explicit: str | None) -> str:
    """The daemon's base URL — explicit, then the env chain, then the default."""
    if explicit:
        return explicit.rstrip("/")
    direct = os.environ.get("ENSEMBLE_DAEMON_URL")
    if direct:
        return direct.rstrip("/")
    for name in ("DAEMON_PORT", "PORT"):
        port = os.environ.get(name)
        if port and port.strip().isdigit():
            return f"http://127.0.0.1:{port.strip()}"
    return DEFAULT_BASE_URL


def read_version(install_dir: str | None) -> str:
    """The installed release version — for the CLI's header line.

    Uses ONLY the ``current`` symlink under the EXPLICIT install dir.
    Never derives a path from ``$HOME``.
    """
    if not install_dir:
        return ""
    current = Path(install_dir) / "releases" / "current"
    try:
        target = os.readlink(current)
    except OSError:
        return ""
    name = os.path.basename(target.rstrip("/"))
    return name if name.startswith("v") else ""


def _request(
    url: str,
    *,
    token: str | None,
    method: str = "GET",
    timeout: float = 15.0,
) -> tuple[int, Any]:
    """One HTTP request — returns ``(status_code, parsed_body)``."""
    request = urllib.request.Request(url, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"wedge_capture: transport error: {exc}", file=sys.stderr)
        raise _TransportError(str(exc)) from exc
    try:
        return status, json.loads(body) if body else {}
    except (TypeError, ValueError):
        return status, {"raw": body}


class _TransportError(RuntimeError):
    """The daemon could not be reached."""


def _error_token(payload: Any) -> str:
    """The typed refusal token out of the house error envelope."""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, str):
            return error
    error = payload.get("error")
    return error if isinstance(error, str) else ""


def _error_message(payload: Any) -> str:
    """The human message out of the house error envelope."""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str):
            return message
    message = payload.get("message")
    return message if isinstance(message, str) else ""


def cmd_list(args: argparse.Namespace) -> int:
    """The capture list."""
    base = resolve_base_url(args.base_url)
    url = f"{base}{API_PREFIX}?limit={int(args.limit)}"
    status, payload = _request(url, token=args.token)
    if status >= 400:
        token = _error_token(payload)
        message = _error_message(payload)
        if token in REFUSAL_TOKENS:
            print(f"wedge_capture: REFUSED — reason={token}: {message}", file=sys.stderr)
            return EXIT_NOT_FOUND
        print(f"wedge_capture: HTTP {status}: {message or payload}", file=sys.stderr)
        return EXIT_TRANSPORT
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return EXIT_OK
    captures = payload.get("captures", []) if isinstance(payload, dict) else []
    if not captures:
        print("no captures")
        return EXIT_OK
    print(
        f"{'capture_id':<52} {'captured_at':<26} {'reason':<22} "
        f"{'mode':<8} {'bytes':>10}"
    )
    for row in captures:
        print(
            f"{str(row.get('capture_id', '')):<52} "
            f"{str(row.get('captured_at', '')):<26} "
            f"{str(row.get('trigger_reason', '')):<22} "
            f"{str(row.get('auto_continue_mode', '')):<8} "
            f"{str(row.get('total_bytes', '')):>10}"
        )
    print(f"\n{len(captures)} capture(s) — source={payload.get('source', '?')}")
    return EXIT_OK


def cmd_get(args: argparse.Namespace) -> int:
    """One capture's payload summary."""
    base = resolve_base_url(args.base_url)
    url = f"{base}{API_PREFIX}/{args.capture_id}"
    status, payload = _request(url, token=args.token)
    if status == 404:
        token = _error_token(payload) or "not_found"
        print(
            f"wedge_capture: REFUSED — reason={token}: "
            f"{_error_message(payload)}",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND
    if status >= 400:
        print(
            f"wedge_capture: HTTP {status}: "
            f"{_error_message(payload) or payload}",
            file=sys.stderr,
        )
        return EXIT_TRANSPORT
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return EXIT_OK
    manifest = payload.get("manifest", {}) if isinstance(payload, dict) else {}
    print(f"capture_id : {payload.get('capture_id')}")
    print(f"capture_dir: {payload.get('capture_dir')}")
    print(f"reason     : {manifest.get('trigger_reason')}")
    print(f"source     : {manifest.get('trigger_source')}")
    print(f"captured_at: {manifest.get('captured_at')}")
    print(f"boot_epoch : {manifest.get('boot_epoch')}")
    print(f"mode       : {payload.get('auto_continue_mode')}")
    print(f"host       : {manifest.get('host')}")
    print(f"summary    : {manifest.get('summary')}")
    artifacts = payload.get("artifacts", {}) if isinstance(payload, dict) else {}
    print("artifacts  :")
    for name, body in artifacts.items():
        status_text = body.get("status") if isinstance(body, dict) else "?"
        print(f"  - {name:<28} {status_text}")
    return EXIT_OK


def cmd_capture(args: argparse.Namespace) -> int:
    """SUBMIT a manual capture — 202 + the pre-allocated id.

    The capture is ENQUEUED; the watchdog services it on its next tick.
    This command does NOT wait for completion — read the outcome with
    ``list`` / ``get``.
    """
    base = resolve_base_url(args.base_url)
    url = f"{base}{API_PREFIX}/capture"
    status, payload = _request(url, token=args.token, method="POST")
    if status == 202:
        print("capture ENQUEUED (the watchdog services it on its next tick)")
        print(f"  capture_id : {payload.get('capture_id')}")
        print(f"  capture_dir: {payload.get('capture_dir')}")
        print(f"  reason     : {payload.get('reason')}")
        print("  read the outcome with: wedge_capture.py list / get <id>")
        return EXIT_OK
    if status >= 400:
        token = _error_token(payload)
        message = _error_message(payload)
        if token in REFUSAL_TOKENS:
            print(
                f"wedge_capture: REFUSED — reason={token}: {message}",
                file=sys.stderr,
            )
            return EXIT_NOT_FOUND
        print(f"wedge_capture: HTTP {status}: {message or payload}", file=sys.stderr)
        return EXIT_TRANSPORT
    print(json.dumps(payload, indent=2, default=str))
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """The watchdog status + the effective auto-continue mode."""
    base = resolve_base_url(args.base_url)
    url = f"{base}{API_PREFIX}/status"
    status, payload = _request(url, token=args.token)
    if status >= 400:
        print(
            f"wedge_capture: HTTP {status}: "
            f"{_error_message(payload) or payload}",
            file=sys.stderr,
        )
        return EXIT_TRANSPORT
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return EXIT_OK
    if not isinstance(payload, dict):
        print(payload)
        return EXIT_OK
    print(f"enabled          : {payload.get('enabled')}")
    print(f"running          : {payload.get('running')}")
    print(f"auto_continue    : {payload.get('auto_continue_mode')}")
    print(f"boot_epoch       : {payload.get('boot_epoch')}")
    print(f"missed_samples   : {payload.get('missed_samples')}")
    print(f"last_capture     : {payload.get('last_capture_time')}")
    print(f"last_reason      : {payload.get('last_capture_reason')}")
    print(f"lag_probe_state  : {payload.get('lag_probe_state')}")
    print(f"last_probe_age_s : {payload.get('last_probe_age_s')}")
    pool = payload.get("pool")
    if isinstance(pool, dict):
        print("pool             :")
        for key in (
            "pool_counters_source",
            "pool_checked_out",
            "pool_size_current",
            "pool_max",
            "pool_waiting_clients",
            "pool_checkout_timeouts",
            "pool_checkout_timeouts_reason",
            "pool_requests_errors",
        ):
            if key in pool:
                print(f"  {key:<32} {pool.get(key)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """The CLI's argument parser."""
    # The connection flags live on a PARENT parser shared by every
    # subparser: argparse only accepts a top-level flag BEFORE the
    # subcommand, so a parent parser is what makes
    # ``wedge_capture.py status --base-url ...`` (the documented operator
    # form) work as well as the flag-first form. Without this, the
    # subcommand form exited 2 on every invocation.
    # ``argument_default=SUPPRESS`` is load-bearing: with the SAME flags on
    # the parent and on every subparser, an unset subparser flag would
    # OTHERWISE overwrite a value parsed before the subcommand with its
    # own default (argparse shares one namespace). Suppressing the default
    # means "not given here" never clobbers "given earlier"; the resolved
    # defaults are filled in after parsing, below.
    common = argparse.ArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS
    )
    common.add_argument(
        "--base-url",
        help=(
            "the daemon's base URL (default: ENSEMBLE_DAEMON_URL / "
            "DAEMON_PORT / PORT, else http://127.0.0.1:9797)"
        ),
    )
    common.add_argument(
        "--token",
        help="bearer token — only needed when auth is ON",
    )
    common.add_argument(
        "--install-dir",
        help=(
            "the install dir — used ONLY for the version display "
            "(default: the INSTALL_DIR env var). NEVER derived from $HOME."
        ),
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="raw JSON passthrough",
    )
    common.add_argument(
        "--limit",
        type=int,
        default=50,
        help="the list page size (default 50)",
    )

    parser = argparse.ArgumentParser(
        prog="wedge_capture.py",
        description=(
            "Read the daemon's wedge-capture evidence store (a thin HTTP "
            "client over /api/wedge-captures)."
        ),
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="the capture list (the default)", parents=[common])
    get_parser = sub.add_parser(
        "get", help="one capture's payload", parents=[common]
    )
    get_parser.add_argument("capture_id", help="the capture id")
    sub.add_parser(
        "capture",
        help="submit a manual capture (202 + the pre-allocated id)",
        parents=[common],
    )
    sub.add_parser("status", help="the watchdog status", parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    """The CLI's entry point."""
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # The default subcommand is ``list`` — ``wedge_capture.py`` with no
    # arguments (or only flags) must not print usage. A subcommand
    # ANYWHERE in argv suppresses the rewrite: prepending ``list`` to a
    # flag-first argv that already names a subcommand produced
    # ``list --base-url … status`` and exited 2.
    known_commands = {"list", "get", "capture", "status"}
    if not any(token in known_commands for token in argv):
        argv = ["list", *argv]
    args = parser.parse_args(argv)
    # Fill the connection-flag defaults that SUPPRESS withheld (see the
    # parent-parser comment in ``build_parser``).
    for name, value in (
        ("base_url", None),
        ("token", None),
        ("install_dir", None),
        ("json", False),
        ("limit", 50),
    ):
        if not hasattr(args, name):
            setattr(args, name, value)
    command = args.command or "list"

    install_dir = args.install_dir or os.environ.get("INSTALL_DIR")
    version = read_version(install_dir)
    if version and command == "status":
        print(f"# daemon release: {version}")

    handlers = {
        "list": cmd_list,
        "get": cmd_get,
        "capture": cmd_capture,
        "status": cmd_status,
    }
    handler = handlers.get(command)
    if handler is None:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    try:
        return handler(args)
    except _TransportError:
        return EXIT_TRANSPORT
    except KeyboardInterrupt:  # pragma: no cover
        return EXIT_TRANSPORT


if __name__ == "__main__":
    sys.exit(main())
