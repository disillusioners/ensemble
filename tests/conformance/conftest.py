"""Conformance-kit fixtures — pointing resolution and the per-test center.

The kit is pointable at ANY center with the FIVE pointing keys
(``CONFORMANCE_CENTER_URL`` / ``_TOKEN`` / ``_CA_PEM`` / ``_MACHINE_ID``
/ ``_EXPECTED_AGENT``) plus the TWO begin-hook keys
(``CONFORMANCE_BEGIN_CMD`` / ``CONFORMANCE_BEGIN_URL``) and the ONE
strict knob (``CONFORMANCE_STRICT_CLOSE_CODE``) — eight keys, pinned by
``test_peer_selftest.py``.  Each key also has a pytest option mirror
(``--conformance-center-url`` etc.); an explicit CLI option beats the
environment.

MODE
----
* **pointed** — ``CONFORMANCE_CENTER_URL`` is set: every test talks to
  THAT center.  Session-dependent areas additionally need a begin hook
  (B8a); without one they FAIL FAST with the named gap.
* **self-host** — no center URL: the kit boots its own in-kit reference
  center per test and tests ITSELF.  This is a SELF-CONSISTENCY run
  (it proves the kit is right; it says nothing about your center) —
  COVERAGE.md states this plainly, and the banner below says it loudly.

``--confcutdir=tests/conformance`` keeps the repo-root conftest (the
mcp-SDK ``sys.modules`` mock and the autouse ``State`` patch) out of
this process — the kit must not inherit the daemon lane's scaffolding.

This conftest reads env/args ONLY: it resolves pointing, boots the
in-kit center via ``conformance_peer`` in self-host mode, and hands the
protocol work to that module.  It contains no wire vocabulary.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from .conformance_peer import (
    BEGIN_HOOK_KEYS,
    begin_on_target,
    InKitCenter,
    Target,
    resolve_target,
)

_OPTION_TO_ENV = {
    "--conformance-center-url": "CONFORMANCE_CENTER_URL",
    "--conformance-token": "CONFORMANCE_TOKEN",
    "--conformance-ca-pem": "CONFORMANCE_CA_PEM",
    "--conformance-machine-id": "CONFORMANCE_MACHINE_ID",
    "--conformance-expected-agent": "CONFORMANCE_EXPECTED_AGENT",
    "--conformance-begin-cmd": "CONFORMANCE_BEGIN_CMD",
    "--conformance-begin-url": "CONFORMANCE_BEGIN_URL",
    "--conformance-strict-close-code": "CONFORMANCE_STRICT_CLOSE_CODE",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("conformance", "the remote-hands conformance kit")
    for option, env_key in _OPTION_TO_ENV.items():
        group.addoption(
            option,
            action="store",
            dest=env_key.lower(),
            default=None,
            metavar="VALUE",
            help=f"mirror of ${env_key} (an explicit option beats the environment)",
        )


def _effective_env(config: pytest.Config) -> dict[str, str]:
    env = dict(os.environ)
    for option, env_key in _OPTION_TO_ENV.items():
        value = config.getoption(env_key.lower(), default=None)
        if value is not None:
            env[env_key] = str(value)
    return env


def pytest_report_header(config: pytest.Config) -> list[str]:
    target = resolve_target(_effective_env(config))
    if target.mode == "self-host":
        return [
            "conformance kit: SELF-CONSISTENCY MODE (no CONFORMANCE_CENTER_URL) — "
            "the kit boots its own IN-KIT reference center and tests ITSELF. "
            "This proves the kit, NOT your center. Point it at a real center "
            "with CONFORMANCE_CENTER_URL (see scripts/conformance/README.md)."
        ]
    env = _effective_env(config)
    hooks = [
        f"{key}={env.get(key, '') or '<unset>'}"
        for key in BEGIN_HOOK_KEYS
        if env.get(key)
    ]
    return [
        f"conformance kit: POINTED at {target.url}",
        f"conformance begin hook: {', '.join(hooks) if hooks else '<NONE — session areas will fail fast (B8a)>'}",
    ]


@pytest.fixture
async def target(request: pytest.FixtureRequest) -> Any:
    """The center under test — pointed via the keys, or the in-kit one."""

    resolved: Target = resolve_target(_effective_env(request.config))
    if resolved.mode == "self-host":
        center = InKitCenter()
        await center.start()
        resolved.center = center
        resolved.url = center.url
        resolved.token = center.token
        resolved.ca_pem = str(center.cert_path)
        resolved.machine_id = center.machine_id
        resolved.expected_agent = center.agent_id
        try:
            yield resolved
        finally:
            await center.stop()
        return
    yield resolved


@pytest.fixture
def strict_close(request: pytest.FixtureRequest) -> bool:
    env = _effective_env(request.config)
    return env.get("CONFORMANCE_STRICT_CLOSE_CODE", "") in ("1", "true", "yes", "on")


class ServingSession:
    """A peer serving loop kept alive for the duration of a block."""

    def __init__(self, peer: Any, task: asyncio.Task) -> None:
        self.peer = peer
        self.task = task

    async def __aenter__(self) -> "ServingSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    async def stop(self) -> None:
        if not self.task.done():
            self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


@pytest.fixture
def serving():
    """Return an async context manager that runs a peer's serve loop."""

    def _serve(peer: Any, **kwargs: Any) -> ServingSession:
        task = asyncio.create_task(peer.serve(**kwargs))
        return ServingSession(peer, task)

    return _serve


@pytest.fixture
def begin(target: pytest.FixtureRequest):
    """The begin trigger for the current target (hook / in-process driver).

    Session-dependent tests call ``await begin()`` while a serving loop
    runs.  In pointed mode without a hook this FAILS FAST (B8a) rather
    than skipping silently.
    """

    async def _begin() -> Any:
        return await begin_on_target(target)

    return _begin
