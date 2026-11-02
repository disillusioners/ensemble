#!/usr/bin/env python3
"""Read-only Plane MCP probe helper — lane `plane-pm-issue-tools`, Phase 1.

LIVES IN THE PLANNING DIR ON PURPOSE: it is **never shipped**. It is a
verification instrument, not daemon code (``phase1-plan.md`` §2).

Design constraints (all binding, from the frozen plan set):

* **P-G2 — read-only by construction.** The verb allow-list below is
  exhaustive; any other verb raises ``ProbeRefusal`` and exits non-zero.
  There is no code path that can issue a mutating verb.
* **The bearer is never printed, logged, or persisted.** The script
  reads ``PLANE_MCP_API_KEY`` from an env file, uses it **only** as an
  outbound credential, and prints key **names** only. No exception is
  made for error paths: the HTTP body of a 401 is printed, and that body
  contains no secret (it is the upstream's ``invalid_token`` envelope).
* **No daemon, no boot, no port.** This is a direct JSON-RPC call
  against the remote Plane MCP endpoint over the ``streamable-http``
  transport (``daemon/mcp/builtin_servers/plane.py`` → ``get_base_config``).

Usage::

    # gate (i) — tools/list only
    python probe_plane_mcp.py --verb tools-list --out capture.json

    # gate (ii) — the single authorised read verb (leader ruling G0)
    python probe_plane_mcp.py --verb list-projects --workspace e2e \
        --allow-list-projects --out projects.json

Exit codes: ``0`` success · ``2`` refusal (verb not allowed) ·
``3`` transport/HTTP failure (the JSON report carries the status).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# --------------------------------------------------------------------------
# P-G2 — the exhaustive verb allow-list. Read-only. No mutating verb exists
# in this file, so the helper cannot be coaxed into one.
# --------------------------------------------------------------------------
ALLOWED_VERBS = ("tools/list", "list_projects")

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "plane-pm-phase1-probe"
CLIENT_VERSION = "1.0.0"

# Env var NAMES only — values never appear in output.
ENV_PLANE_URL = "PLANE_MCP_URL"
ENV_PLANE_KEY = "PLANE_MCP_API_KEY"
ENV_PLANE_SLUG = "PLANE_MCP_WORKSPACE_SLUG"

DEFAULT_ENV_FILE = "/home/nea/ensemble-src/.env"


class ProbeRefusal(Exception):
    """Raised when the requested verb is not on the read-only allow-list."""


# --------------------------------------------------------------------------
# env-file loading (KEY=VALUE, quotes stripped). Values are never printed.
# --------------------------------------------------------------------------
def load_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a dotenv file into a dict. Values are used, never printed."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"probe: env file not found: {p}")
    out: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


# --------------------------------------------------------------------------
# streamable-http JSON-RPC plumbing
# --------------------------------------------------------------------------
def _parse_body(response: httpx.Response) -> Any:
    """Return the JSON payload of a JSON or SSE-framed JSON-RPC response."""
    ctype = response.headers.get("content-type", "")
    text = response.text
    if "text/event-stream" in ctype.lower():
        # SSE: pull the last complete ``data:`` JSON frame.
        frames: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                frames.append(line[5:].strip())
        for frame in reversed(frames):
            if not frame:
                continue
            try:
                return json.loads(frame)
            except json.JSONDecodeError:
                continue
        raise ProbeRefusal("SSE response carried no parseable JSON frame")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ProbeRefusal(f"non-JSON response body: {exc}") from exc


class PlaneMcpProbe:
    """Minimal streamable-http MCP client, scoped to read-only verbs."""

    def __init__(self, url: str, api_key: str, workspace_slug: str, timeout: float):
        self._url = url
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Authorization": f"Bearer {api_key}",
        }
        if workspace_slug:
            self._headers["x-workspace-slug"] = workspace_slug
        self._session_id: str | None = None
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)
        self._req_id = 0

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PlaneMcpProbe":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport -------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return self._client.post(self._url, json=payload, headers=headers)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def initialize(self) -> tuple[int, Any, int]:
        """``initialize`` → ``notifications/initialized``.

        Returns ``(http_status, jsonrpc_body, initialized_notification_status)``.
        The JSON-RPC body is returned **unwrapped** so the caller can read
        ``result.serverInfo`` / ``result.protocolVersion`` from it.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        }
        resp = self._post(payload)
        status = resp.status_code
        try:
            body = _parse_body(resp)
        except ProbeRefusal:
            body = None
        if status >= 400:
            return status, body, 0
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        # notifications/initialized — 202/200 expected, body ignored.
        note = self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return status, body, note.status_code

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            payload["params"] = params
        resp = self._post(payload)
        status = resp.status_code
        try:
            body = _parse_body(resp)
        except ProbeRefusal as exc:
            return status, {"_error": str(exc)}
        return status, body

    def tools_list(self) -> tuple[int, Any]:
        return self.rpc("tools/list")

    def call_read_tool(self, name: str, arguments: dict[str, Any]) -> tuple[int, Any]:
        return self.rpc("tools/call", {"name": name, "arguments": arguments})


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="probe_plane_mcp.py",
        description="Read-only Plane MCP probe (lane plane-pm-issue-tools, Phase 1).",
    )
    p.add_argument("--verb", required=True, choices=["tools-list", "list-projects"],
                   help="the read-only operation to perform")
    p.add_argument("--workspace", default=None,
                   help="workspace slug for list_projects (default: from the env file)")
    p.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                   help="dotenv file carrying the PLANE_* keys (default: the armed main checkout)")
    p.add_argument("--out", default=None, help="write the JSON report here")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--allow-list-projects", action="store_true",
                   help="explicit opt-in for the single authorised read verb "
                        "(leader ruling G0; without it the verb is refused)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # ---- P-G2: read-only by construction -------------------------------
    requested = "tools/list" if args.verb == "tools-list" else "list_projects"
    if requested not in ALLOWED_VERBS:
        print(f"REFUSED: verb {requested!r} is not on the read-only allow-list",
              file=sys.stderr)
        return 2
    if requested == "list_projects" and not args.allow_list_projects:
        print("REFUSED: list_projects requires --allow-list-projects "
              "(the single authorised read verb; leader ruling G0)", file=sys.stderr)
        return 2

    env = load_env_file(args.env_file)
    url = env.get(ENV_PLANE_URL, "")
    api_key = env.get(ENV_PLANE_KEY, "")
    slug = args.workspace or env.get(ENV_PLANE_SLUG, "")
    if not url or not api_key:
        missing = [n for n, v in ((ENV_PLANE_URL, url), (ENV_PLANE_KEY, api_key)) if not v]
        report = {
            "ok": False,
            "refused": True,
            "reason": "missing_required_env",
            "missing_env_names": missing,
            "env_file": args.env_file,
        }
        _emit(report, args.out)
        return 3

    report: dict[str, Any] = {
        "ok": False,
        "verb": requested,
        "workspace_slug_used": slug,
        "env_file": args.env_file,
        "env_names_read": [ENV_PLANE_URL, ENV_PLANE_KEY, ENV_PLANE_SLUG],
        "credential_state": "unknown",
    }

    with PlaneMcpProbe(url, api_key, slug, args.timeout) as probe:
        init_status, init_body, initialized_status = probe.initialize()
        report["initialize_http_status"] = init_status
        report["initialized_notification_http_status"] = initialized_status
        if init_status >= 400:
            report["credential_state"] = "expired_or_rejected" if init_status == 401 else "http_error"
            report["error_body"] = init_body
            _emit(report, args.out)
            return 3
        report["credential_state"] = "accepted"
        if isinstance(init_body, dict):
            result = init_body.get("result") or {}
            server_info = result.get("serverInfo") or {}
            report["server_info"] = {
                "name": server_info.get("name"),
                "version": server_info.get("version"),
                "protocolVersion": result.get("protocolVersion"),
            }

        if requested == "tools/list":
            status, body = probe.tools_list()
            report["tools_list_http_status"] = status
            if status >= 400:
                report["error_body"] = body
                _emit(report, args.out)
                return 3
            tools = ((body or {}).get("result") or {}).get("tools") or []
            report["ok"] = True
            report["tool_count"] = len(tools)
            report["tools"] = tools
        else:  # list_projects
            status, body = probe.call_read_tool("list_projects", {})
            report["list_projects_http_status"] = status
            report["raw_result"] = body
            report["ok"] = status < 400

    _emit(report, args.out)
    return 0 if report.get("ok") else 3


def _emit(report: dict[str, Any], out: str | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
        # stdout carries only a redacted summary — never the payload.
        summary = {k: v for k, v in report.items() if k not in ("tools", "raw_result")}
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        print(text)


if __name__ == "__main__":
    raise SystemExit(main())
